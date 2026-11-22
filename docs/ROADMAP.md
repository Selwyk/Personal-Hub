# Evolución de Personal Hub

Presente: Core/MFA, Dashboard/Diario, catálogos, Tareas V0.1 y aislamiento por instancia.
Futuro: mejorar capacidades propias sin requerir cambios simultáneos en otro repositorio.
Calendario de eventos, notificaciones, alarmas e interfaz de asistente siguen pendientes; no son dependencias básicas.
PH-CORE-004 prepara la activación personal real con autorización y aceptación separadas.

No fusionar otros proyectos del propietario. Sin commits, remotos, releases o despliegues implícitamente autorizados.
Los resultados heredados de pruebas no se consideran validación de un cambio nuevo; consultar los informes PH-CORE-003 y PH-CORE-004 para sus alcances y evidencias.

Tras la activación y las tres comprobaciones del propietario: priorizar PH-TASKS-002
(recurrencias por serie/ocurrencia y fechas civiles), después subtareas, calendario
personal, notificaciones locales y asistente de escritorio. Reutilizar los controles
existentes y mantener las funciones esenciales sin proveedor externo. La propuesta
detallada y los pendientes operativos están en work/PH-CORE-004/report.md.

P1 de mantenimiento: 14 avisos heredados npm en tooling (11 moderados/3 altos), cero en producción según la auditoría del encargo; revisar de forma acotada y repetir pruebas. Revisar deprecación TestClient/httpx. Antes de publicar, revisar licencia, procedencia y paquetes/imágenes de release.
