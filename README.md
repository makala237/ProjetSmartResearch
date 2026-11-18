# SmartResearch FMSB / CAMES 🩺🎓

Plateforme web d'intelligence artificielle dédiée à l'élaboration, la rédaction, la validation, la saisie clinique et la soutenance de protocoles de recherche médicale conformes aux normes d'excellence de la **Faculté de Médecine et des Sciences Biomédicales (FMSB - Université de Yaoundé I)** et du **CAMES**.

---

## 🌟 Architecture Globale (4 Modules Intégrés)

### 🔬 Module 1 : Recherche, Rédaction & Validation étape par étape
- **Canevas officiel FMSB / CAMES** : Découpage canonique rigoureux des sections (Introduction, Justification, Problématique, Objectifs, Revue de la littérature, Méthodologie, Échantillonnage selon Schwartz, Éthique, etc.).
- **Orchestration IA Multi-Modèles** :
  - **Gemini 3.5 Flash Lite / Gemini 3.1 Flash Lite** : Rédaction scientifique de précision et résumés structurés.
  - **Gemini Decision** : Arbitrage méthodologique, contrôle de conformité académique et directives de mise en forme.
- **Recherche Bibliographique Médicale Intégrée** : Interrogation automatique de PubMed, Europe PMC et OpenAlex avec formatage des citations selon la norme **Vancouver**.

### 📄 Module 2 : Studio d'Édition & Export Word (.docx) / PDF (.pdf)
- **Moteur de mise en page déterministe** : Format A4, marges académiques de 2,5 cm, police Times New Roman, interligne 1,5, texte justifié.
- **Génération automatique des pièces annexes** : Page de garde bilingue officielle, Table des matières dynamique, Liste des tableaux et Liste des figures.
- **Export haute fidélité** : Fichiers `.docx` (gabarit maître officiel) et `.pdf` prêts pour soumission institutionnelle.

### 📋 Module 3 : Masque de Saisie CRF & Base de Données Patients
- **Générateur automatique de fiche de collecte (CRF)** : Extraction des variables d'étude et construction de l'Annexe 1.
- **Masque de saisie interactif** : Enregistrement, validation en temps réel et suivi de cohorte clinique.
- **Export analytique** : Téléchargement instantané de la base de données au format `.csv` pour exploitation sous SPSS, R ou Epi-Info.

### 🖥️ Module 4 : Diaporama & Soutenance (PowerPoint FMSB / CAMES)
- **Squelette canonique de soutenance (17 Diapositives)** : Issu de la matrice officielle de protocole (`tamplatePowerpointProtocole.pptx`).
  1. *Accueil & Bienvenue solennelle*
  2. *Page de Garde officielle FMSB* (Logos UY1, en-têtes bilingues, département, titre encadré, matricule, directeurs)
  3. *Plan du Diaporama (6 grands axes)*
  4. *Introduction (1/2) : Définition & Contexte Clinique*
  5. *Introduction (2/2) : Épidémiologie & Justification*
  6. *Objectif Général*
  7. *Objectifs Spécifiques (1/2)*
  8. *Objectifs Spécifiques (2/2)*
  9. *Méthodologie (1/6) : Type, Lieux & Cadre d'Étude*
  10. *Méthodologie (2/6) : Durée & Période d'Étude*
  11. *Méthodologie (3/6) : Population & Critères d'Inclusion*
  12. *Méthodologie (4/6) : Critères de Non-Inclusion & Éthique*
  13. *Méthodologie (5/6) : Procédure & Déroulement de l'Étude*
  14. *Méthodologie (6/6) : Variables & Analyse Statistique*
  15. *Chronogramme des Activités (Gantt 3D 2026–2027)*
  16. *Budget Prévisionnel & Financement (Diagramme Radial)*
  17. *Remerciements & Clôture Académique*
- **Templates graphiques haute définition (16:9)** : Arrière-plans scientifiques spécialisés intégrés au millimètre près.
- **Zones de texte transparentes sans contour** : Textes vectoriels 100% éditables dans Microsoft PowerPoint placés au premier plan.
- **Notes de présentation orale intégrées** : Discours chronométré (45 à 60s par diapositive) pré-rédigé pour guider l'impétrant le jour J.
- **Export direct en un clic** : Téléchargement du fichier PowerPoint `.pptx` haute résolution.

---

## 🚀 Installation & Démarrage Local

### Prérequis
- **Python 3.11+**
- **Node.js 18+** & npm

### 1. Cloner le dépôt
```bash
git clone https://github.com/makala237/ProjetSmartResearch.git
cd ProjetSmartResearch
```

### 2. Configuration des variables d'environnement
Créez un fichier `.env` dans le dossier `backend/` :
```env
GEMINI_API_KEY=votre_cle_api_gemini
PORT=8001
```

### 3. Backend (FastAPI)
```bash
cd backend
python -m venv .venv
# Sur Windows :
.\.venv\Scripts\activate
# Sur Linux/macOS :
# source .venv/bin/activate

pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

### 4. Frontend (React + TypeScript + Tailwind + Vite)
Dans un nouveau terminal :
```bash
cd frontend
npm install
npm run build
```
L'application compilée est servie directement par le serveur FastAPI sur **http://127.0.0.1:8001**.
Pour le serveur de développement Vite avec Hot Reload :
```bash
npm run dev
```

---

## 🛠️ Stack Technique
- **Backend** : FastAPI, Pydantic v2, Python-PPTX, Python-Docx, Uvicorn.
- **Frontend** : React 18, TypeScript, Tailwind CSS, Lucide Icons, Vite.
- **Modèles IA** : Google Gemini API (Gemini 3.5 Flash Lite, Gemini 3.1 Flash Lite, Gemini Decision).
- **Standards** : Normes académiques FMSB (Université de Yaoundé I), Référentiel CAMES, Style Vancouver.

---

## 👤 Auteur
**makala237** — [GitHub](https://github.com/makala237)
