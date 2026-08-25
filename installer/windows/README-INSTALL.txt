SOUL Core para Windows
======================

Este instalador incluye su propio Python y TODAS las dependencias Python declaradas
por SOUL Core: memoria SQLite, embeddings semánticos, índice ANN, integridad
criptográfica, clientes PostgreSQL/pgvector y Neo4j, y conexión a Ollama. No
necesita instalar Python, pip ni Git.

Al finalizar, "Configurar mi alma" permite elegir una plantilla oficial, ponerle
nombre y comprobar el modelo de Ollama. Las almas se guardan en:

  %USERPROFILE%\.soul

Desinstalar SOUL Core NO borra esa carpeta ni sus memorias.

Ollama se usa desde DADITOGAMER por Tailscale. PostgreSQL y Neo4j son servicios
externos opcionales: el EXE incluye sus clientes Python, no instala esos servidores.
