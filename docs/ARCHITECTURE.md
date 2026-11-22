# Arquitectura de Personal Hub

Monolito modular propio: frontend -> /api/v1 -> backend -> PostgreSQL. Este repositorio construye y ejecuta todos sus componentes.
No importa código ni instala paquetes desde el otro repositorio. Autenticación/MFA, catálogos y utilidades se mantienen inicialmente en cada árbol.

Responsabilidades presentes: Core, Dashboard, Diario, revisión/importación genérica, Project/Tag, Tareas y herramientas de instancia/backup.
El diario y las etiquetas son personales. No se incluye ningún provider profesional ni el comando activity.

Las cuatro revisiones Alembic heredadas se conservan sin reescritura. Evolucionan desde aquí en cada repositorio: usar revisiones nuevas propias, nunca aplicar migraciones de otro producto.
Los esquemas inicialmente coinciden por continuidad técnica; las bases, identidades, credenciales, sesiones, redes, índices y copias no se comparten.
El modo de instancia valida el dominio fijo de este producto además de UUID/base/rol. Project/Tag se reutilizan dentro de su propia base.

Tareas conserva versión optimista, bloqueo de fila, título/descripción/estado/prioridad, fecha civil opcional, relaciones, completado/reapertura y exportación JSON.
parent_id es reserva no editable. Recurrencias, calendario de eventos y alarmas no se han implementado.
El calendario actual representa entradas del Diario.

La configuración del propietario y sus preferencias genéricas deben mantenerse separadas de los registros y credenciales de un entorno.
El administrador del host/Docker es una frontera de confianza; no se promete aislamiento frente a él.
