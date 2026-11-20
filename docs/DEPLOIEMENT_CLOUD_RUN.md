# Guide de Déploiement Firebase Hosting + Google Cloud Run (Smart Research)

Ce guide décrit le déploiement complet de **Smart Research** avec l'architecture recommandée **Firebase Hosting + Google Cloud Run** :
- **Firebase Hosting** ([firebase.json](file:///c:/Users/MBIANDA/Desktop/ProjetSmartResearch/firebase.json), [.firebaserc](file:///c:/Users/MBIANDA/Desktop/ProjetSmartResearch/.firebaserc)) : sert l'interface React/Vite PWA (`frontend/dist`) sur CDN mondial HTTPS et redirige `/api/**` et `/tamplates/**` vers Cloud Run.
- **Google Cloud Run** ([Dockerfile](file:///c:/Users/MBIANDA/Desktop/ProjetSmartResearch/Dockerfile), [cloudbuild.yaml](file:///c:/Users/MBIANDA/Desktop/ProjetSmartResearch/cloudbuild.yaml)) : exécute l'API FastAPI, les appels **Gemini**, la génération `.docx`/`.pptx` ainsi que **LibreOffice Headless + Poppler (`pdftoppm`)** pour les exports PDF et aperçus PNG.
- **Cloud SQL PostgreSQL** (ou Neon / Supabase) : base utilisateurs, rôles, licences, quotas et protocoles.
- **Google Cloud Storage** (`STORAGE_BACKEND=gcs`) : stockage persistant des logos de couverture, figures et exports.
- **Google Secret Manager** : stockage sécurisé de `DATABASE_URL`, `JWT_SECRET_KEY`, `GEMINI_API_KEY` et `BOOTSTRAP_ADMIN_PASSWORD`.

---

## 1. Architecture conteneurisée & Hébergement hybride Firebase

1. **Frontend React/Vite (Firebase Hosting)** :
   - Compilé localement via `npm --prefix frontend run build` dans `frontend/dist`.
   - `firebase.json` configure :
     - `/api/**` → rewrite vers Cloud Run `smart-research-api` (`europe-west1`)
     - `/tamplates/**` → rewrite vers Cloud Run `smart-research-api` (`europe-west1`)
     - `**` → fallback SPA `/index.html`
   - **Important (Timeout Firebase Hosting)** : les rewrites Cloud Run via Firebase Hosting ont une limite de **60 secondes par requête HTTP**. Pour garantir des exports PDF/PPTX rapides sans dépassement ni démarrage à froid (*cold start*), déployez `smart-research-api` avec **`--cpu 2 --memory 4Gi`** et idéalement **`--min-instances 1`** en production.

2. **Backend FastAPI + Moteur Documentaire Linux (Cloud Run)** :
   - Le [Dockerfile](file:///c:/Users/MBIANDA/Desktop/ProjetSmartResearch/Dockerfile) installe `libreoffice-writer`, `libreoffice-impress`, `poppler-utils` (`pdftoppm`) et les polices métriques (`fonts-liberation`, `fonts-liberation2`, `fonts-crosextra-carlito`, `fonts-crosextra-caladea`, `fonts-dejavu-core`).
   - Exécute les migrations Alembic au démarrage via [docker/entrypoint.sh](file:///c:/Users/MBIANDA/Desktop/ProjetSmartResearch/docker/entrypoint.sh).
   - Expose le contrôle de santé `/api/health` (`app`, `database_dialect`, `render_engine`, `storage_backend`, `soffice_available`, `pdftoppm_available`).

---

## 2. Pré-requis Cloud (dans un même projet Firebase / GCP, forfait Blaze)

1. Créer le projet sur la **console Firebase**, activer le **forfait Blaze** et noter son identifiant (`PROJECT_ID`).
2. Activer les APIs Google Cloud nécessaires :
   ```bash
   export PROJECT_ID="votre-projet-firebase"
   export REGION="europe-west1"
   export SERVICE="smart-research-api"

   gcloud services enable \
     run.googleapis.com \
     cloudbuild.googleapis.com \
     artifactregistry.googleapis.com \
     sqladmin.googleapis.com \
     storage.googleapis.com \
     secretmanager.googleapis.com \
     firebasehosting.googleapis.com \
     --project "${PROJECT_ID}"
   ```
3. **Base de données PostgreSQL** (Cloud SQL, Neon ou Supabase) :
   - Renseigner `DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/smartresearch?sslmode=require` dans Secret Manager (`SMR_DB_URL`).
   - Pour Cloud SQL, ajouter `--add-cloudsql-instances="${PROJECT_ID}:${REGION}:smartresearch-pg"` au déploiement Cloud Run.
4. **Stockage binaire persistant (`STORAGE_BACKEND=gcs`)** :
   - Créer un bucket Cloud Storage (ex. `${PROJECT_ID}-smartresearch-uploads`) et attribuer `roles/storage.objectAdmin` au compte de service Cloud Run.
5. **Secrets obligatoires dans Secret Manager** :
   - `SMR_DB_URL` (`DATABASE_URL`)
   - `SMR_JWT_SECRET` (`JWT_SECRET_KEY`, au moins 64 caractères aléatoires)
   - `SMR_GEMINI_KEY` (`GEMINI_API_KEY`)
   - `SMR_ADMIN_PASS` (`BOOTSTRAP_ADMIN_PASSWORD`)

---

## 3. Build, Déploiement Cloud Run & Déploiement Firebase Hosting

```bash
# 1. Configurer l'identifiant Firebase dans .firebaserc (remplacer VOTRE_ID_PROJET_FIREBASE)
firebase use "${PROJECT_ID}"

# 2. Construire et pousser l'image Docker dans le cloud via Cloud Build
gcloud artifacts repositories create cloud-run-source-deploy \
  --repository-format=docker --location="${REGION}" --project="${PROJECT_ID}" || true

gcloud builds submit \
  --project "${PROJECT_ID}" \
  --config cloudbuild.yaml \
  --substitutions "_REGION=${REGION},_SERVICE_NAME=${SERVICE},_CLOUD_STORAGE_BUCKET=${PROJECT_ID}-smartresearch-uploads"

# 3. Déployer / mettre à jour Cloud Run avec les secrets + Cloud SQL + min-instances=1 en prod
gcloud run deploy "${SERVICE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${REGION}-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE}:latest" \
  --platform managed \
  --allow-unauthenticated \
  --cpu 2 \
  --memory 4Gi \
  --timeout 300 \
  --min-instances 1 \
  --max-instances 5 \
  --set-env-vars "RENDER_ENGINE=libreoffice,STORAGE_BACKEND=gcs,CLOUD_STORAGE_BUCKET=${PROJECT_ID}-smartresearch-uploads,RUN_MIGRATIONS_ON_START=true" \
  --set-secrets "DATABASE_URL=SMR_DB_URL:latest,JWT_SECRET_KEY=SMR_JWT_SECRET:latest,GEMINI_API_KEY=SMR_GEMINI_KEY:latest,BOOTSTRAP_ADMIN_PASSWORD=SMR_ADMIN_PASS:latest"

# 4. Compiler le frontend React/Vite puis déployer sur Firebase Hosting
npm --prefix frontend run build
firebase deploy --only hosting --project "${PROJECT_ID}"
```

---

## 4. Vérification après déploiement

1. Tester l'API via l'URL publique Firebase Hosting (`https://${PROJECT_ID}.web.app/api/health`) :
   ```bash
   curl -fsS "https://${PROJECT_ID}.web.app/api/health"
   ```
   Vérifier que `"app": "Smart Research"`, `"database_dialect": "postgresql"`, `"render_engine": "libreoffice"`, `"storage_backend": "gcs"`, `"soffice_available": true` et `"pdftoppm_available": true`.

2. Ouvrir `https://${PROJECT_ID}.web.app` (et `https://${PROJECT_ID}.firebaseapp.com`), se connecter en administrateur, créer un compte étudiant avec page de garde verrouillée et tester un export Word / PDF / PPTX.

3. Pour un contrôle local avant déploiement :
   ```bash
   backend\.venv\Scripts\python.exe scripts\compare_pdf_png_render.py --project-id 5221c9f3-65e
   ```
