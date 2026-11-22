# Contenido del repositorio público

La primera subida a Selwyk/Personal-Hub publica código Personal 1.2.0, migraciones,
pruebas, locks, herramientas genéricas, contrato de trabajo y documentación portable.
La procedencia y decisión de extracción se describen en el ADR 0001. No se copia
el historial del repositorio original ni fuentes exclusivas de Work Hub.

Los expedientes locales work/PH-CORE-003 y work/PH-CORE-004 quedan excluidos de
Git porque contienen inventarios y evidencias operativas privadas. Se conservan
íntegros en el checkout del propietario; no se borran ni se sustituyen por una
versión pública incompleta. Un clon del remoto no contiene esos expedientes ni
constituye una copia de seguridad de la documentación operativa privada.

También quedan fuera perfiles, .env reales, claves, certificados, datos, dumps,
copias cifradas, logs privados, ZIP generados, dependencias instaladas y artefactos
de ejecución. El .env.example y las credenciales sintéticas de pruebas son ejemplos,
no credenciales operativas. Nuevos informes deben revisarse antes de versionarlos:
no incluir datos del propietario, topología doméstica ni conexiones profesionales.

La autorización de subir el código permite el commit inicial y push a este remoto.
No autoriza despliegues, publicaciones de imágenes, releases, cambios del host,
migraciones reales, nuevas conexiones ni envío de datos privados. La activación
personal y su aceptación funcional siguen pendientes e independientes de GitHub.

La publicación no cambia las limitaciones del producto: backup en memoria limitado
a 512 MiB de dump; recuperación real/TLS/ACL pendientes de verificar en cada destino;
avisos conocidos de herramientas npm y limitación del lock completo en Windows.
No se ha concedido una licencia pública adicional. Las dependencias conservan sus
propias licencias; antes de redistribuir, revisar las obligaciones aplicables.
