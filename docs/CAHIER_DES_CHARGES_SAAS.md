# CAHIER DES CHARGES — SmartResearch FMSB (Édition SaaS Commerciale)

**Version :** 1.0 (Validée le 19 novembre 2026)  
**Plateforme :** SmartResearch FMSB / CAMES  
**Architecture cible :** FastAPI + PostgreSQL (SQLite local en dev) + React/Tailwind PWA + Google Cloud Run

---

## 1. Vision & Objectifs Produit
Transformer l'application SmartResearch FMSB (Modules 1 à 4 : Recherche & Rédaction, Édition Word/PDF, Masque de Saisie Patients, Soutenance PowerPoint) en une **plateforme SaaS multi-utilisateurs sécurisée**, commercialisable auprès des étudiants rédigeant leur protocole de thèse, accessible sur **PC et Smartphone**, pilotée depuis un **Portail Administrateur** complet et adossée à une **base de données en ligne**.

---

## 2. Rôles, Authentification & Sécurité Multi-Utilisateurs

### 2.1 Rôles système
| Rôle | Création du compte | Nombre de protocoles | Droits & Accès |
| :--- | :--- | :--- | :--- |
| **`admin`** | Compte initial via variables d'environnement + création par un autre `admin` | Illimité (tests, démos, accompagnement) | Tous les modules + **Portail Administrateur (`/admin`)** |
| **`student`** | **100 % manuelle par l'administrateur** (aucune auto-inscription libre) | **Strictement 1 seul protocole** | Uniquement son propre protocole, selon ses licences actives par module |

### 2.2 Sécurité d'accès & anti-partage de compte
- **Mots de passe** : hachage cryptographique fort (**Argon2id** via `argon2-cffi`), jamais stockés ni journalisés en clair.
- **Première connexion sécurisée** : l'administrateur attribue un mot de passe temporaire (copiable en 1 clic) ; le drapeau `must_change_password = true` oblige l'étudiant à définir son mot de passe personnel dès sa première connexion avant tout accès aux modules.
- **Authentification JWT** : jeton d'accès signé (`HS256`, durée configurable) + jeton de rafraîchissement contrôlé.
- **Session unique active (anti-partage)** : un identifiant `active_session_id` est régénéré à chaque connexion. Si un second appareil se connecte avec les mêmes identifiants, le jeton précédent est immédiatement révoqué (`HTTP 401 SESSION_REVOKED`), et l'événement (compteur + IP) est tracé pour l'administrateur.
- **Cloisonnement absolu des données** :
  - Chaque protocole, diaporama, figure, fiche patient et fichier exporté est lié à un `owner_user_id`.
  - Chaque route `/api/projects/{project_id}/...` vérifie côté serveur `project.owner_user_id == current_user.id` (sinon `HTTP 403 PROJECT_FORBIDDEN`).
  - Les assets binaires (images, schémas SVG, aperçus PNG, exports CSV/DOCX/PDF/PPTX) passent exclusivement par des endpoints authentifiés.

---

## 3. Verrouillage Strict « 1 Utilisateur = 1 Protocole » (Verrou Immédiat)
1. **Unicité en base** : tout compte `student` est limité à **1 seul protocole** (`HTTP 403 ONE_PROTOCOL_LIMIT` en cas de seconde création). La création d'autres projets et la suppression (`DELETE /api/projects/{project_id}`) sont réservées aux administrateurs.
2. **Verrouillage immédiat dès la création (`topic_locked = true`)** :
   - Dès la création du protocole d'un étudiant, les champs identitaires sont immédiatement figés :
     - `name` (nom interne du protocole)
     - `cover.title` (titre officiel sur la page de garde)
     - `cover.author_name` (nom de l'impétrant)
     - `cover.matricule` (matricule universitaire — capturé et verrouillé dès qu'il est saisi)
   - Toute modification ultérieure de ces champs par un étudiant via `PATCH /cover` ou `POST /import` est bloquée côté serveur (`HTTP 403 TOPIC_LOCKED`).
3. **Déblocage / modification encadrée (Admin uniquement)** : un administrateur peut modifier un titre verrouillé sur justification (ex. demande du directeur de thèse), avec traçabilité dans `admin_audit_logs`.

---

## 4. Licences Granulaires par Module & Comportement à Expiration

### 4.1 Les 4 modules licenciables
Chaque étudiant possède **4 dates d'expiration indépendantes** (`user_module_entitlements`) :
- **`module1` — Rédaction & Recherche** : recherche bibliographique, segmentation, rédaction/audit IA, tableaux, schémas, annexes, import.
- **`module2` — Édition Word / PDF** : mise en forme, données structurées, audit global, validation Vancouver, génération TOC/listes, export `.docx`/`.pdf`.
- **`module3` — Masque de Saisie (CRF) & Patients** : édition du CRF, saisie/suppression de fiches patients terrain, export CSV.
- **`module4` — Diaporama & Soutenance (PowerPoint)** : édition des slides, génération IA, audit CAMES, aperçus PNG, export `.pptx`/`.pdf`.

### 4.2 Catalogue de plans & cumul des prolongations
- L'administrateur définit ses offres commerciales (`license_plans`) : nom, prix (FCFA), durée en jours pour chacun des 4 modules (`module1_days`..`module4_days`) et quota d'appels IA inclus (`ai_quota_included`).
- Lors d'un renouvellement avant échéance, la prolongation s'ajoute à la date restante : `max(maintenant, expires_at_actuel) + N jours`.

### 4.3 Règle à expiration : Lecture seule & Export conservés
| Type d'opération sur un module expiré | Comportement backend |
| :--- | :--- |
| **Consultation & téléchargement des travaux déjà faits** (lecture, aperçus, exports Word/PDF/CSV/PPTX) | **Autorisé (`200 OK`)** |
| **Édition, ajout/suppression de données, import** | **Bloqué (`403 MODULE_EXPIRED`)** |
| **Générations, audits et suggestions IA** | **Bloqué (`403 MODULE_EXPIRED`)** |

---

## 5. Quotas d'Appels IA & Télémétrie
- Chaque plan attribue un solde de **crédits IA** (`user_ai_quotas`), rechargeable manuellement par l'administrateur.
- Barème par action IA (débité uniquement en cas de succès) :
  - **1 crédit** : audit de section, traduction département, suggestion tableau/schéma, régénération IA d'1 slide, audit CAMES du diaporama.
  - **2 crédits** : recherche bibliographique, segmentation, rédaction de section, correction IA, audit global, validation Vancouver.
  - **5 crédits** : génération IA complète du diaporama (`generate-all`).
- Rejet préventif (`HTTP 403 AI_QUOTA_EXCEEDED`) si solde insuffisant avant tout appel Gemini.
- Enregistrement des actions IA et exports dans `usage_events`.

---

## 6. Schéma de Base de Données (PostgreSQL Cloud / SQLite Local)
Géré via **SQLAlchemy 2.0 + Alembic** (colonne `JSONB` sur PostgreSQL, `JSON` sur SQLite pour développement local) :
1. `users` : identité, rôle, mot de passe Argon2id, `must_change_password`, `active_session_id`, `session_switch_count`, dernière connexion/IP.
2. `license_plans` : catalogue commercial (durées M1..M4, prix FCFA, crédits IA).
3. `user_license_grants` : historique des attributions, montants payés et références (ex. reçu Mobile Money).
4. `user_module_entitlements` : dates d'effet et d'expiration par `(user_id, module_key)`.
5. `user_ai_quotas` : `credits_total`, `credits_used`.
6. `projects` : protocole complet (`data_json`), propriétaire, verrou `topic_locked` et compteurs dénormalisés (`sections_validated_count`, `patients_count`).
7. `presentations` : diaporama associé (`data_json`), version et nombre de slides.
8. `usage_events` : journal d'utilisation par module, action, modèle Gemini et coût en crédits.
9. `admin_audit_logs` : journal des interventions administrateur.

---

## 7. Portail Administrateur (`/admin`)
Accessible uniquement au rôle `admin` :
1. **KPIs globaux** : étudiants actifs / expirant < 7 j / expirés, revenus cumulés (FCFA), crédits IA consommés, protocoles & fiches patients.
2. **Gestion des Utilisateurs (`student` / `admin`)** : création manuelle avec mot de passe temporaire copiable, attribution/prolongation de licence, ajustement par module, recharge crédits IA, déverrouillage/modification du titre verrouillé, réinitialisation mot de passe, suspension.
3. **Catalogue des Plans de Licence** : CRUD complet des formules tarifaires et durées modulaires.
4. **Menu Étudiants & Statistiques** : suivi individuel (progression `X/24`, fiches patients, exports, crédits IA restants, historique d'activité et commutations de session).

---

## 8. Interface Adaptative PC & Smartphone (Responsive + PWA)
- Écran de connexion + flux obligatoire de changement de mot de passe à la 1re connexion.
- Badges permanents de validité par module (`Actif — X j` / `Lecture seule — Expiré`) et jauge de crédits IA.
- Navigation tactile inférieure sur mobile (`< 768px`), tiroirs coulissants pour M1/M4, **Module 3 (Masque de Saisie)** optimisé pour l'usage terrain d'une main à l'hôpital, et manifeste PWA installable.

---

## 9. Hébergement Cloud Run & Rendu PDF / PNG
- Conteneur Docker unique **Google Cloud Run** + base **PostgreSQL** managée + stockage objet **Cloud Storage / R2**.
- Les `.docx` et `.pptx` restent générés en Python pur (100 % identiques à Windows).
- Conversion PDF & aperçus PNG sélectionnable via `RENDER_ENGINE=auto|com|libreoffice|cloud_api` : LibreOffice Headless (+ polices métriques Times/Arial + `pdftoppm`) sur Cloud Run, avec validation visuelle comparative et option API de conversion Office activable en variable d'environnement.

---

## 10. Feuille de Route (5 Phases)
- **Phase 1 — Socle DB, Auth & Isolation** : SQLAlchemy + Alembic, migration JSON → DB, JWT session unique, protection des 60 routes + verrou immédiat « 1 user = 1 protocole ».
- **Phase 2 — Licences par Module, Quotas IA & API Admin** : plans/entitlements/quotas/télémétrie, lecture seule à expiration, routes `/api/admin/...`.
- **Phase 3 — Frontend Auth, Licences & Portail Admin** : écran Login, changement mot de passe, badges licences/quotas, UI `/admin`.
- **Phase 4 — Adaptation Mobile/PC (Responsive + PWA)** : barre mobile, tiroirs M1/M4, refonte mobile-first Module 3 terrain.
- **Phase 5 — Cloud Run, Stockage & Rendu Linux** : Docker Cloud Run + LibreOffice/poppler, stockage cloud et validation comparative PDF/PNG.
