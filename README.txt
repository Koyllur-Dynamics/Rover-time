ROVER-TIME — public package

Solo site/ se publica en GitHub Pages. La simulación y los visores Python se ejecutan en el navegador. Edu, las salas y el acceso a datos lunares requieren el Worker indicado en publication.json.

En GitHub: Settings > Pages > Source: GitHub Actions. No hacen falta secretos de Cloudflare ni Groq en este repositorio.

Preparar desde Nav - web con Tools/prepare_web_publish.ps1. No editar site/ manualmente: contiene hashes verificables.
Los archivos mayores de 100 MiB se guardan en partes de 48 MiB. GitHub Actions verifica sus hashes y los reconstruye antes de publicar. No ejecutar scripts/large-files.mjs en el repositorio local: la reconstrucción es solo para el despliegue.
El proyecto Unity, las partidas y el servidor no pertenecen a este repositorio.
