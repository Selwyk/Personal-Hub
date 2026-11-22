# ADR 0001 — Extracción independiente

Estado: decisión de PH-CORE-003, 2026-11-21. El software es del propietario y no pertenece a una empresa.

Se extrae Personal Hub desde un snapshot no comprometido del proyecto anterior. El original permanece intacto.
No se copia .git ni su historial porque no se ha auditado para redistribución y contiene una evolución mezclada con trabajo local.
Se inicia Git vacío, sin commits ni remotos. La procedencia del código y las migraciones se conserva mediante hashes y documentación del encargo.
No se pierde la referencia recuperable original. No se copian configuraciones de usuario, datos, imports, copias, índices ni artefactos de ejecución.

Se conserva la cadena Alembic y el comportamiento probado de los módulos reutilizados. No se reescriben revisiones aplicadas.
Core se mantiene inicialmente en cada repositorio. La duplicación inicial es deliberada y revisable; no hay referencias de ejecución al otro árbol.
Los espacios profesionales son otro nivel de separación distinto al que existe entre estos dos productos.
