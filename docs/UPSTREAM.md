# Traslado de cambios genéricos

Cada repositorio conserva código común local con ciclo de versiones propio. No hay paquete compartido, submódulo, symlink o import de fuentes del otro árbol.
Para trasladar una mejora de Core, comparar el diff mínimo, revisar que no contiene datos/identidad de producto/proveedor, adaptar dentro del destino y ejecutar sus pruebas.
Registrar la decisión y origen del cambio en un ADR o encargo. No copiar carpetas completas ni historiar secretos.
Una corrección de seguridad común puede merecer traslado a ambos, pero una funcionalidad propia no exige modificar al otro.
Valorar una librería versionada solo si el coste de mantenimiento lo justifica y existe autorización; no se crea un tercer repositorio automáticamente.
