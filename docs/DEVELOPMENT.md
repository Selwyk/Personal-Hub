# Desarrollo y pruebas independientes

Frontend, desde frontend:

    npm ci
    npm run lint
    npm run typecheck
    npm test
    npm run build

Backend reproducible, desde la raíz propia:

    docker compose -f compose.test.yaml up -d --wait db
    docker compose -f compose.test.yaml build tests
    docker compose -f compose.test.yaml run --rm tests pytest -q -p no:cacheprovider
    docker compose -f compose.test.yaml run --rm tests ruff check --no-cache app tests migrations
    docker compose -f compose.test.yaml run --rm tests alembic check

Establecer COMPOSE_DISABLE_ENV_FILE=1. Es un proyecto de prueba propio con PostgreSQL tmpfs y credenciales sintéticas.
Nunca apuntar pytest a una base existente: los fixtures truncan tablas. No copiar .env, datos, node_modules ni entornos virtuales de otro checkout.
Las imágenes Docker instalan sus locks con hashes dentro de este repositorio; no precisan un checkout hermano.
Los tests Windows del collector, donde exista, se ejecutan por separado con unittest y temporales privados.

La prueba e2e/smoke.spec.ts usa exclusivamente una instancia nueva marcada synthetic y su perfil privado.
HUB_SMOKE_PROFILE apunta a su instance.json; HUB_E2E_URL identifica su origen; Chromium debe estar instalado en el entorno de prueba.
El arranque y la configuración de esos fixtures se describen en DEPLOYMENT.md.

Regresión E2E heredada, cada escenario en proyecto Docker nuevo y PostgreSQL tmpfs:

    python scripts/run-e2e.py --mode tasks --port 15673
    python scripts/run-e2e.py --mode journal --port 15673
    python scripts/run-e2e.py --mode bootstrap --port 15673

Construir previamente las imágenes propias con target test y web indicadas en DEPLOYMENT.md. Instalar Chromium de Playwright en el entorno de prueba; se admite PLAYWRIGHT_BROWSERS_PATH dentro de .runtime para evitar instalación global. El runner detiene únicamente su proyecto UUID nuevo; no borra contenedores o datos ajenos. Verificar que el puerto elegido está libre.

Smoke nuevo: npx playwright test e2e/smoke.spec.ts, desde frontend, con HUB_SMOKE_PROFILE y HUB_E2E_URL del perfil synthetic nuevo. Ejecutarlo sobre datos inicialmente vacíos. Los casos heredados tienen flags explícitos y solo los habilita el runner correspondiente. Un simple npm run test:e2e sin fixtures no sustituye estas pruebas.

Ensayo opcional de pareja: disponer de carpetas personal/ y work/ con perfiles synthetic nuevos, lanzar el smoke propio de cada producto y ejecutar frontend/e2e/instances.spec.ts con HUB_ISOLATION_ROOT. Después, desde cada raíz, ejecutar python scripts/test-instance-isolation.py --root RAIZ_DE_PERFILES de forma secuencial. Comprueba redes/roles/datos/reinicio y restaura solo la base synthetic del producto actual. No usar con perfiles o datos reales.

python scripts/check-boundaries.py revisa fronteras/privacidad/importaciones. python scripts/validate-agent-work.py work/ID_DEL_ENCARGO valida el contrato de un expediente disponible, no las pruebas del producto. Los expedientes PH-CORE-003/004 y sus evidencias operativas son locales y no están incluidos en el repositorio público. Véase [contenido publicado](PUBLICATION.md).

python deploy/check-privacy.py conserva la comprobación heredada de exclusiones Git y de posibles valores privados locales; solo imprime conteos y nombres de archivo. Complementa el análisis de fronteras.
