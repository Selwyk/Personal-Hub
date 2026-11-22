# Contrato de Trabajo con Agentes v1.0 — borrador revisado

**Propósito.** Acordar un método verificable y reutilizable para dirigir desarrollos con cualquier agente de software sin repetir instrucciones ni trasladar manualmente su estado entre conversaciones. Es independiente de proveedor, modelo y aplicación de seguimiento.

## 1. Autoridad y fuentes de verdad

- **Usuario o responsable técnico:** decide objetivos, riesgos aceptables, excepciones y autorizaciones. La revisión humana no puede fingirse ni inferirse de un archivo.
- **Agente ejecutor (Codex, OpenCode u otro):** investiga, propone, implementa en el ámbito autorizado, prueba, documenta y mantiene la instantánea técnica. Un cambio de ejecutor no cambia el contrato ni las autorizaciones.
- **Repositorio:** fuente de verdad para el código, historial y cambios efectivos. Una afirmación en el informe no reemplaza un diff o commit.
- **Azure DevOps (si aplica):** fuente de verdad para el Work Item, sus campos, estados, conversaciones y aprobaciones formales. El contrato puede enlazarlo, nunca sustituirlo.
- **AI Workbench (futuro):** consumidor de estados y enlaces; no requisito de ejecución ni fuente autoritativa de cambios.
- **Personal Hub (futuro):** posible consumidor de resúmenes autorizados; mantener separación laboral/personal y minimizar información sensible.

Precedencia: instrucciones de sistema, organización, usuario y proyecto que correspondan; este contrato establece reglas operativas por defecto sin sustituirlas. Si hay conflicto material, detener la acción afectada y explicarlo.

## 2. Un encargo, tres archivos de base y un plan por etapas

Cada tarea estructurada reside en `work/<id>/`:

1. `task.yaml` — **entrada relativamente estable**: identidad, objetivo, alcance, exclusiones, invariantes, criterios de aceptación y referencias. Cambiarla requiere explicar y aprobar el nuevo alcance cuando afecte a compromisos previos.
2. `plan.yaml` — **estructura de entregas**, generado al terminar la planificación: lista ordenada de etapas con valor autónomo, entregables, criterios de aceptación y dependencias. Es obligatorio antes de implementar, aunque una tarea pequeña pueda tener una sola etapa.
3. `state.json` — **instantánea reemplazable** de ejecución: fase interna, estado de las etapas entregables, criterios, pruebas, riesgos, aprobaciones pendientes, archivos tocados y próximo paso. No es una auditoría inmutable.
4. `report.md` — **narración verificable**: análisis, decisiones con sus motivos, plan, cambios, comandos o procedimiento de prueba, evidencias y conclusiones. Los enlaces deben apuntar a evidencias reales accesibles y autorizadas.

Durante `intake`, `discovery` y `planning`, `plan.yaml` todavía puede no existir y `state.delivery_plan` será `null`. Antes de pasar a `implementation`, ambos deben estar presentes y ser coherentes. Para tareas triviales, una sola etapa con valor claro evita burocracia. El esquema de intercambio permanece constante.

Los ID de tarea son únicos y estables dentro del conjunto de proyectos, sin depender del identificador de DevOps. Fechas en ISO 8601 con zona horaria explícita (`Z` o desplazamiento).

## 3. Modos de autonomía

| Modo | Conducta predeterminada | Intervención humana |
| --- | --- | --- |
| `supervised` | Descubrir y planificar; implementar después de aprobación | Plan y decisiones relevantes antes de actuar |
| `balanced` **(defecto)** | Avanzar automáticamente en análisis, plan, implementación y pruebas de cambios ordinarios dentro del repositorio | Plan previo en tareas de alto riesgo; cambios de alcance y operaciones reservadas |
| `autonomous` | Completar tareas acotadas y de bajo riesgo sin revisiones intermedias | Operaciones reservadas, bloqueos o cambios de alcance |

El modo no amplía permisos. Aunque el usuario seleccione `autonomous`, una operación reservada requiere autorización expresa y específica.

**Riesgo:** `low`, `medium` o `high`. Considerar alto riesgo si afecta datos persistentes, seguridad, permisos, varias integraciones críticas, compatibilidad contractual, despliegues, migraciones o producción. Si el riesgo observado supera al declarado, elevarlo y revisar las puertas de aprobación antes de continuar.

## 4. Fases internas, estados y transiciones

Fases internas de trabajo (`stage`) en orden normal; **no confundir con etapas del plan entregables**:

1. `intake`: aclarar objetivo, límites y criterios verificables.
2. `discovery`: inspeccionar código, dependencias, historial y entorno autorizado; sin cambios externos.
3. `planning`: estrategia, riesgos, plan de pruebas, posibles efectos colaterales.
4. `implementation`: cambios acotados, con pruebas durante el desarrollo.
5. `validation`: ejecutar pruebas aplicables, comparar resultados, registrar evidencia y límites.
6. `handoff`: informe, decisiones, pendientes y solicitud de revisión si corresponde.
7. `closed`: entrega técnica concluida y documentada; no equivale necesariamente a aceptación formal del usuario.

Los estados (`status`) son `queued`, `in_progress`, `awaiting_approval`, `blocked`, `done`, `cancelled`.

- Registrar siempre `next_action` y actualizar `updated_at` tras cada transición material.
- `awaiting_approval` exige `pending_approval` concreto; no continuar con la acción reservada.
- `blocked` exige `blockers` y alternativas razonables. El agente puede seguir con partes independientes si no aumenta el riesgo.
- `done` exige `stage: closed`, criterios de aceptación resueltos, **todas las etapas vigentes del plan completas** y sin pruebas relevantes fallidas, bloqueos ni aprobaciones pendientes. El usuario conserva el derecho a rechazar la entrega: `user_acceptance` se registra por separado (`unreviewed`, `accepted`, `rejected`).
- `closed` corresponde a `done` o `cancelled`. Una entrega rechazada no puede seguir marcada como `done`; reabrir o crear trabajo correctivo según el alcance.
- Una tarea con incertidumbres relevantes no verificadas debe permanecer en `handoff`/`blocked` o `awaiting_approval`, no marcarse como `done`.
- Permitir volver a una etapa anterior con motivo documentado. Nunca falsificar progreso para satisfacer un validador.

## 4.1. Etapas de entrega del plan: valor y seguimiento

Tras el descubrimiento, el agente debe descomponer la solución en **etapas de valor independiente**. No son pasos internos como «abrir el IDE», «analizar» o «compilar»: cada una entrega algo que puede revisarse, demostrarse o aprovecharse aunque el resto del plan todavía no termine.

Cada etapa de `plan.yaml` declara:

- **ID y título estable**, orden y dependencias explícitas.
- **Valor:** qué capacidad, decisión verificable o reducción de riesgo aporta por sí misma.
- **Entregables:** código, pruebas, documento, prototipo o capacidad observable.
- **Criterios de aceptación:** referencias a IDs definidos en `task.yaml` y evidencias exigidas.

Preferir entre 2 y 7 etapas razonablemente pequeñas; admitir una sola para tareas simples y más si el alcance lo exige. El agente debe evitar dividir por capas técnicas sin resultados comprobables para el usuario. Todas las etapas deben tener al menos un criterio y la totalidad de los criterios de la tarea deben tener una etapa responsable. Las dependencias deben apuntar a etapas anteriores, evitando ciclos.

Los **estados de etapa entregable** (diferentes del estado global) son:

| Estado | Significado | Indicador de UI |
| --- | --- | --- |
| `pending` | No iniciada | Punto gris |
| `in_progress` | Se está ejecutando | Punto amarillo |
| `awaiting_review` | Entregable preparado, pendiente de revisión o autorización | Punto azul |
| `completed` | Entregable verificado con evidencia | Punto verde |
| `blocked` | No puede avanzar por una causa registrada | Punto rojo |
| `cancelled` | Etapa cancelada por cambio autorizado del plan | Gris tachado |

El agente actualiza la etapa al iniciar trabajo material, al detectar un bloqueo, al entregar algo para revisión y tras verificar su cierre. No usa porcentajes inventados. Una etapa `completed` requiere evidencia y todos sus criterios `passed` o `not_applicable` justificados; las dependencias deben haberse completado. No se marca el plan completo hasta que todas las etapas vigentes están completadas.

Replanificar requiere aumentar `plan_revision`, documentar el motivo y preservar el historial de decisiones. Si cambia el alcance, los invariantes o las aceptaciones originales, solicitar la aprobación aplicable. Un fichero de estado generado por el agente no constituye aprobación humana.

El panel puede visualizar el estado en todo momento, pero debe tratarlo como lectura de archivos; el contrato no presupone una API o aplicación concreta. Véase `panel/PLAN-PANEL-SPEC.md`.

## 5. Puertas de aprobación (obligatorias)

Solicitar una autorización que identifique **acción, objetivo, entorno y riesgos** antes de:

1. Desplegar, importar soluciones, publicar cambios o modificar configuraciones en cualquier entorno externo, incluido DEV, UAT y PRO.
2. Escribir datos de negocio o permisos en Dataverse, SharePoint, Azure DevOps u otro servicio externo. Leer usando credenciales configuradas y dentro de permisos sí es admisible cuando el encargo lo requiere.
3. Borrar, migrar, sobrescribir o ejecutar acciones difíciles de revertir; alterar esquemas persistentes o introducir cambios masivos.
4. Ampliar el alcance, relajar un invariante o introducir una diferencia funcional no autorizada.
5. Ejecutar un plan de riesgo `high` en modo `balanced`; en `supervised` se requiere plan aprobado para implementar cualquier cambio.

Una orden previa específica y vigente puede satisfacer la puerta cuando identifica inequívocamente esos elementos; **un campo escrito por el agente en `state.json` no crea autorización**. Si una política corporativa impide la acción, detenerse y no intentar eludir el bloqueo. Las operaciones normales de edición de archivos dentro del repositorio de trabajo autorizado no requieren confirmación fase por fase en `balanced` o `autonomous`.

## 6. Control de alcance y calidad

- Inspeccionar antes de editar; respetar cambios ajenos, configuración existente, convenciones y pruebas actuales.
- Explicitar `in_scope`, `out_of_scope`, `invariants` y `acceptance_criteria`; no confundir solución sugerida con requisito aprobado.
- No corregir bugs conocidos fuera de alcance sin anotarlo y obtener aprobación cuando cambie el comportamiento.
- Diferenciar verificaciones: análisis estático, compilación, unitarias, integración, regresión, comparación funcional, ejecución real en entorno y UAT.
- Cada criterio de aceptación debe tener un resultado (`passed`, `failed`, `blocked`, `not_run`, `not_applicable`) y una evidencia o justificación en `state.json` y `report.md`.
- Cada test ejecutado registra método/comando, entorno, resultado y evidencia. No convertir una prueba sintética en evidencia de funcionamiento real en producción.
- Los tests listados como parte del plan deben llegar a `passed` o `not_applicable` antes de `done`; los no realizados no pueden ocultarse como éxito. Registrar las pruebas no ejecutadas en el informe con su motivo.
- Si una prueba no se puede realizar, indicarlo explícitamente, su impacto y quién debe completarla. No inventar accesos o resultados.
- Preferir pruebas reproducibles, con datos sintéticos, aislamiento y limpieza segura. Nunca usar datos sensibles en informes o fixtures.
- Comprobar cambios de compatibilidad y no regresión; si la equivalencia es el objetivo, registrar las diferencias aceptadas por separado de los fallos.

**Definition of Done técnico:** alcance implementado; etapas entregables `completed`; criterios `passed` o `not_applicable` con justificación válida; pruebas críticas ejecutadas y sin fallos abiertos; evidencias accesibles; diff revisado; documentación, `plan.yaml` y `state.json` actualizados; ninguna aprobación pendiente. Cuando se haya autorizado explícitamente una excepción a un criterio, registrar la excepción en el informe y en el criterio antes de cerrarlo. Sin excepción, no ocultar pendientes.

## 7. Entrega y reanudación

Al llegar a `handoff`, emitir un resumen en español con objetivo, cambios, cobertura de pruebas, evidencia, riesgos residuales, decisiones, limitaciones y siguiente acción. Si no hay bloqueo, cerrar técnicamente conforme a la definición anterior. La aceptación del usuario se registra de forma separada; no modificar automáticamente Azure DevOps.

Para reanudar:

1. Leer `task.yaml`, `plan.yaml` si existe, `state.json`, `report.md` y el contrato/perfil.
2. Verificar `git status` y cambios relevantes en el repositorio; comprobar fuentes externas de lectura si son necesarias y están accesibles.
3. Si el estado es antiguo o inconsistente, registrar la discrepancia, no sobrescribir código o estado de terceros, y reconciliar antes de implementar.
4. Retomar `next_action` y la etapa entregable activa solamente si continúa dentro del alcance y sin puerta pendiente.

## 8. Integración futura y privacidad

`state.json` se diseña para lectura de AI Workbench: no contiene tokens ni conversaciones completas. Los consumidores deben tratarlo como una **instantánea no firmada** y contrastar enlaces o commits antes de afirmar que ocurrió una operación. El esquema permite `extensions` namespaced para perfiles futuros sin contaminar el núcleo.

Las decisiones y aprobaciones se anotan en `report.md` con fecha y referencia; no se eliminan silenciosamente al editar el informe. El historial Git aporta trazabilidad si la política del proyecto permite versionar estos artefactos. El estado del Work Item de Azure DevOps se modifica solamente con su flujo de permisos propio.

No registrar secretos, cadenas de conexión, adjuntos confidenciales, PII o datos productivos en el contrato. Respetar las políticas de clasificación de la organización antes de registrar o exportar archivos. Ninguna sincronización entre entornos personales y corporativos se activa por defecto.

## 9. Evolución

- Los esquemas tienen `schema_version: "1.0"`. Cambios compatibles pueden introducir campos opcionales; los incompatibles necesitan versión mayor y migración explícita.
- Mantener validación automática de archivos y comprobaciones semánticas fundamentales.
- Nuevos perfiles tecnológicos solo agregan instrucciones específicas; no reducen puertas de autorización ni rebajan las reglas comunes.


## 10. Reglas de este repositorio

Autonomía equilibrada; conservar cambios ajenos. Encargos significativos usan work/<ID> con task.yaml, plan.yaml, state.json y report.md.
Los YAML de estos archivos se expresan como JSON para validar con la herramienta stdlib scripts/validate-agent-work.py.
Cada etapa del plan lleva status pending/in_progress/passed/failed/blocked; su estado interoperable se mantiene en state.json (passed corresponde a completed).
No efectuar commits, pushes, remotos, publicaciones, despliegues reales, escrituras externas ni operaciones destructivas sin autorización específica.
Datos reales y secretos están fuera de Git; pruebas solo con bases nuevas sintéticas. No borrar espacios profesionales en esta entrega.
La revisión humana y preparación para producción se registran aparte de la entrega técnica.
