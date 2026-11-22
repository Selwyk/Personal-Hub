# Transición desde el repositorio original

## Resultado de la extracción

Los nuevos árboles contienen código seleccionado y Git recién inicializado. No se copiaron historial, datos, secretos, configuración del propietario ni artefactos de ejecución. El checkout original sigue siendo la referencia recuperable del trabajo anterior y concurrente; no se ha archivado ni cambiado su despliegue.

Cada producto instala sus propios locks, construye sus imágenes y conserva su cadena Alembic. La identidad del producto se define en backend/product.json. Las versiones 1.2.0 son iniciales locales, todavía sin publicación; a partir de aquí pueden evolucionar por separado.

## Continuar el desarrollo

1. Abrir el árbol del producto correspondiente y leer su AGENTS.md, CONTRACT.md y work/PH-CORE-003.
2. Ejecutar las pruebas propias descritas en DEVELOPMENT.md. No reutilizar resultados históricos como validación de un cambio nuevo.
3. Mantener cambios específicos en ese producto. Para mejoras genéricas seguir UPSTREAM.md y revisar un diff mínimo sin configuraciones de entorno.
4. Crear revisiones Alembic nuevas sobre b261121 dentro del producto que evoluciona. No copiar revisiones posteriores automáticamente ni modificar las cuatro históricas.
5. Antes de versionar o publicar, revisar secretos, procedencia, licencia y contenido elegible. No hay commits ni remotos autorizados en esta entrega.

## Activación posterior

Personal Hub empezará en una instancia vacía y con identidad nueva. No copiar registros laborales ni conectar esta API a una base existente. Preparar imágenes propias fijadas por digest, un host con permisos privados, un origen HTTPS exclusivo, MFA y destino de backup con clave custodiada aparte. Ejecutar preflight/verify y un ensayo de restauración en ese host antes de declarar separación operacional real.

La nueva aplicación Work Hub tampoco sustituye automáticamente la instalación anterior. Cualquier traslado profesional requiere autorización separada, clasificación de datos, conservación aplicable, exportación revisable, backup y plan de reversión. Tener el mismo esquema no autoriza reusar una base o modificar su marca de identidad.

La activación personal se propone en PH-CORE-004. Ninguna migración real, importación, cambio de identidad, apagado del original o retirada de datos forma parte de PH-CORE-003.
