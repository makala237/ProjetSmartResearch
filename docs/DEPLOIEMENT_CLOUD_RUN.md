# Guide de Déploiement Firebase Hosting + Google Cloud Run + Firestore Enterprise + GCS (Smart Research)

Ce guide décrit le déploiement complet de **Smart Research** avec l'architecture **100% Firebase / Google Cloud** (`DB_BACKEND=firestore` + `STORAGE_BACKEND=gcs`) :
- **Firebase Hosting** ([firebase.json](../firebase.json), [.firebaserc](../.firebaserc)) : sert l'interface React/Vite PWA (`frontend/dist`) sur CDN mondial HTTPS et redirige `/api/**` et `/tamplates/**` vers Cloud Run.
- **Google Cloud Run** ([Dockerfile](../Dockerfile), [cloudbuild.yaml](../cloudbuild.yaml)) : exécute l'API FastAPI, les appels **Gemini**, la génération `.docx`/`.pptx` ainsi que **LibreOffice Headless + Poppler (`pdftoppm`)** pour les exports PDF et aperçus PNG.
- **Cloud Firestore Enterprise Edition** (`database=smartresearch-prod`, `location=europe-west1`) : métadonnées utilisateurs, rôles, sessions, licences, quotas IA, journaux et métadonnées de projets. *(Mode PostgreSQL/SQLite conservé via `DB_BACKEND=sql` + `DATABASE_URL` pour le dev local ou Cloud SQL).*
- **Google Cloud Storage** (`STORAGE_BACKEND=gcs`) : stockage persistant des JSON complets de protocoles/présentations (aucune limite à 1 MiB), des logos de couverture, figures et exports.
- **Google Secret Manager** : stockage sécurisé de `JWT_SECRET_KEY`, `GEMINI_API_KEY` et `BOOTSTRAP_ADMIN_PASSWORD` *(aucun `SMR_DB_URL` requis en mode `firestore`)*.

---

## 1. Architecture conteneurisée & Hébergement hybride Firebase

1. **Frontend React/Vite (Firebase Hosting)** :
   - Compilé localement via `npm --prefix frontend run build` dans `frontend/dist`.
   - `firebase.json` configure :
     - `/api/**` → rewrite vers Cloud Run `smart-research-api` (`europe-west1`)
     - `/tamplates/**` → rewrite vers Cloud Run `smart-research-api` (`europe-west1`)
     - `**` → fallback SPA `/index.html`
   - **Important (Timeout Firebase Hosting)** : les rewrites Cloud Run via Firebase Hosting ont une limite de **60 secondes par requête HTTP**. Pour garantir des exports PDF/PPTX rapides sans dépassement ni démarrage à froid (*cold start*), déployez `smart-research-api` avec **`--cpu 2 --memory 4Gi`** et idéalement **`--min-instances 1`** en production.
   - **Important (Pipeline Tracker en RAM)** : `pipeline_tracker.py` suit l'avancement temps réel en mémoire du conteneur. Gardez `UVICORN_WORKERS=1` et **`--max-instances 1`** tant que le suivi n'est pas externalisé.

2. **Backend FastAPI + Moteur Documentaire Linux (Cloud Run)** :
   - Le [Dockerfile](../Dockerfile) installe `libreoffice-writer`, `libreoffice-impress`, `poppler-utils` (`pdftoppm`) et les polices métriques (`fonts-liberation`, `fonts-liberation2`, `fonts-crosextra-carlito`, `fonts-crosextra-caladea`, `fonts-dejavu-core`).
   - En mode `DB_BACKEND=firestore`, [docker/entrypoint.sh](../docker/entrypoint.sh) ignore Alembic et démarre directement Uvicorn.
   - Expose `/api/health` (`app`, `db_backend`, `database_dialect`, `render_engine`, `storage_backend`, `soffice_available`, `pdftoppm_available`).

---

## 2. Pré-requis Cloud (Projet Firebase / GCP `smart-research-fmsb`, forfait Blaze)

1. Activer les APIs Google Cloud nécessaires :
   ```bash
   export PROJECT_ID="smart-research-fmsb"
   export REGION="europe-west1"
   export SERVICE="smart-research-api"
   export FIRESTORE_DB="smartresearch-prod"

   gcloud services enable \
     run.googleapis.com \
     cloudbuild.googleapis.com \
     artifactregistry.googleapis.com \
     firestore.googleapis.com \
     storage.googleapis.com \
     secretmanager.googleapis.com \
     firebasehosting.googleapis.com \
     --project "${PROJECT_ID}"
   ```
2. **Créer la base Cloud Firestore Enterprise + déployer règles & index** :
   ```bash
   npx -y firebase-tools@latest firestore:databases:create "${FIRESTORE_DB}" \
     --location="${REGION}" \
     --edition="enterprise" \
     --firestore-data-access="ENABLED" \
     --mongodb-compatible-data-access="DISABLED" \
     --project "${PROJECT_ID}"

   npx -y firebase-tools@latest deploy --only firestore --project "${PROJECT_ID}"
   ```
3. **Stockage binaire persistant (`STORAGE_BACKEND=gcs`)** :
   - Créer un bucket Cloud Storage `${PROJECT_ID}-smartresearch-uploads` en région `europe-west1`.
4. **Rôles IAM du compte de service Cloud Run** :
   - Attribuer `roles/datastore.user` (accès backend Server SDK à Firestore Enterprise) et `roles/storage.objectAdmin` (lecture/écriture sur le bucket GCS) au compte de service d'exécution Cloud Run.
5. **Secrets obligatoires dans Secret Manager** :
   - `SMR_JWT_SECRET` (`JWT_SECRET_KEY`, au moins 64 caractères aléatoires)
   - `SMR_GEMINI_KEY` (`GEMINI_API_KEY`)
   - `SMR_ADMIN_PASS` (`BOOTSTRAP_ADMIN_PASSWORD`)

   *(Note : si vous préférez revenir au mode PostgreSQL `DB_BACKEND=sql`, ajoutez `SMR_DB_URL`, passez `DB_BACKEND=sql` et `RUN_MIGRATIONS_ON_START=true`.)*

---

## 3. Build, Déploiement Cloud Run & Déploiement Firebase Hosting

```bash
# 1. Vérifier le projet actif dans .firebaserc
npx -y firebase-tools@latest use "${PROJECT_ID}"

# 2. Construire et pousser l'image Docker dans le cloud via Cloud Build
gcloud artifacts repositories create cloud-run-source-deploy \
  --repository-format=docker --location="${REGION}" --project="${PROJECT_ID}" || true

gcloud builds submit \
  --project "${PROJECT_ID}" \
  --config cloudbuild.yaml \
  --substitutions "_REGION=${REGION},_SERVICE_NAME=${SERVICE},_CLOUD_STORAGE_BUCKET=${PROJECT_ID}-smartresearch-uploads,_DB_BACKEND=firestore,_FIRESTORE_DATABASE=${FIRESTORE_DB}"

# 3. Déployer / mettre à jour Cloud Run avec les secrets + min-instances=1 / max-instances=1
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
  --max-instances 1 \
  --set-env-vars "DB_BACKEND=firestore,FIRESTORE_PROJECT_ID=${PROJECT_ID},FIRESTORE_DATABASE=${FIRESTORE_DB},RENDER_ENGINE=libreoffice,STORAGE_BACKEND=gcs,CLOUD_STORAGE_BUCKET=${PROJECT_ID}-smartresearch-uploads,RUN_MIGRATIONS_ON_START=false" \
  --set-secrets "JWT_SECRET_KEY=SMR_JWT_SECRET:latest,GEMINI_API_KEY=SMR_GEMINI_KEY:latest,BOOTSTRAP_ADMIN_PASSWORD=SMR_ADMIN_PASS:latest"

# 4. Compiler le frontend React/Vite puis déployer sur Firebase Hosting
npm --prefix frontend run build
npx -y firebase-tools@latest deploy --only hosting,firestore --project "${PROJECT_ID}"
```

---

## 4. Vérification après déploiement

1. Tester l'API via l'URL publique Firebase Hosting (`https://${PROJECT_ID}.web.app/api/health`) :
   ```bash
   curl -fsS "https://${PROJECT_ID}.web.app/api/health"
   ```
   Vérifier que `"app": "Smart Research"`, `"db_backend": "firestore"`, `"database_dialect": "firestore"`, `"render_engine": "libreoffice"`, `"storage_backend": "gcs"`, `"soffice_available": true` et `"pdftoppm_available": true`.

2. Ouvrir `https://${PROJECT_ID}.web.app` (et `https://${PROJECT_ID}.firebaseapp.com`), se connecter en administrateur, créer un compte étudiant avec page de garde verrouillée et tester un export Word / PDF / PPTX.

3. Pour un contrôle local avant déploiement :
   ```bash
   backend\.venv\Scripts\python.exe scripts\compare_pdf_png_render.py --project-id 5221c9f3-65e
   ```
