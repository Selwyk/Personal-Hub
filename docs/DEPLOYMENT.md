# Preparación y despliegue de Personal Hub

No se ha desplegado una instalación real de este repositorio. La activación personal sigue pendiente de autorización específica. El expediente operativo PH-CORE-004 permanece exclusivamente en el checkout del propietario, fuera del repositorio público; véase [contenido publicado](PUBLICATION.md).
No mezclar estos archivos, secretos, redes o volúmenes con una instalación anterior ni con el otro producto.

Construcción independiente:

    docker build --target base -t personal-hub-api:local backend
    docker build -f frontend/Dockerfile -t personal-hub-web:local .

Preparación, sin arrancar ni migrar:

    python deploy/instances.py prepare --root RUTA_NUEVA_PRIVADA --domain personal --origin https://app.example.invalid --port 15473 --api-image IMAGEN_API_CON_DIGEST --web-image IMAGEN_WEB_CON_DIGEST

El CLI acepta únicamente el dominio de este producto. Las imágenes reales deben estar fijadas por digest o ID local de contenido sha256 de 64 dígitos, verificado tras transferencia privada; la carpeta no puede existir.
El perfil exige HTTPS, permisos POSIX privados y credenciales distintas de admin/migrador/app, MFA y backup.
Prepare no toca instalaciones existentes y no copia registros. Windows se admite exclusivamente para ensayo synthetic.
En modo synthetic se pueden usar localhost/127.0.0.1 y se incluye un seed de un único propietario de prueba; nunca reinicializa usuarios existentes.

    python deploy/instances.py preflight --root RUTA_NUEVA_PRIVADA
    python deploy/instances.py verify --root RUTA_NUEVA_PRIVADA

Usar siempre el project-name exacto de instance.json y COMPOSE_DISABLE_ENV_FILE=1 al operar Compose.
La API no recibe el migrador, no publica PostgreSQL ni monta carpetas profesionales externas. Sus nuevas conexiones comprueban UUID, dominio, base, rol y privilegios.

Para Nginx Proxy Manager existente, `prepare --proxy-network NOMBRE_RED` conecta únicamente web a una red externa ya creada, con alias único personal-hub-UUID y sin publicar puertos del host. Conserva edge/data propias. En esos perfiles nuevos, db/api/web reinician con unless-stopped y rotan logs 10m × 3; el migrador no reinicia. Preflight/verify comprueban configuración, redes, alias, puertos y políticas efectivas. La red y el Proxy Host HTTPS/ACL del proxy requieren aprobación; prepare no los crea. Sin esa opción, los perfiles anteriores se validan con la plantilla original sin cambios.
Verify contrasta imágenes efectivas, montajes, variables, redes y volumen reales. Preflight --against permite una comprobación opcional de otro perfil por el operador; no crea una dependencia de ejecución entre productos.

Backup/restore preparado, únicamente con autorización para el destino:

    python deploy/instances.py backup --root RUTA_PRIVADA --file backup-AAAA-MM-DD.enc
    python deploy/instances.py restore --root RUTA_PRIVADA --file backup-AAAA-MM-DD.enc --confirm-instance UUID_EXACTO --confirm-domain personal

Copias cifradas con Fernet y clave propia, manifiesto de identidad y hash; sin sobrescritura ni restore cruzado.
La clave y la configuración MFA se custodian aparte. Límite de dump: 512 MiB por el cifrado en memoria.
Restore detiene API/web, restaura transaccionalmente, migra con su rol, revoca sesiones/tokens y verifica antes de reanudar.
Un fallo deja cerrada la entrada; no forzar up ni modificar el comentario de identidad para sortearlo.
La recuperación en otro host necesita la identidad y configuración originales, rutas revisadas, claves, roles e imágenes compatibles.
No se ha validado todavía un destino externo de copias ni TLS/DNS/ACL de producción.

Se propone sustituir el cifrado en memoria por un mecanismo validado en flujo compatible con el sistema privado de backups del operador, conservando identidad, integridad, lectura de copias anteriores y rechazo de restauración cruzada. No quitar solo el límite de 512 MiB: se mantendría el consumo de memoria. Validar primero con datos sintéticos, copias grandes, corrupción y falta de espacio. La configuración del backup real y su custodia no se publican.

No ejecutar import-journal.py o transfer-to-review.py sobre datos existentes sin plan y autorización. No hay borrado de espacios implementado.
