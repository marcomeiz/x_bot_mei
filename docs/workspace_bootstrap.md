# Workspace Bootstrap & Hygiene

Este repositorio evita versionar artefactos generados (JSON intermedios, textos, logs) para mantener el árbol limpio. Los siguientes comandos ayudan a preparar y validar el entorno local de forma reproducible.

## Configuración de entorno

- Selecciona ajustes base via `APP_CONFIG_ENV` (`dev`, `staging`, `prod`) que apuntan a `config/settings.<env>.yaml`.
- Si necesitas un YAML personalizado, usa `APP_CONFIG_PATH=/ruta/al/settings.yaml`.
- Las variables de entorno tienen prioridad sobre los valores definidos en el YAML.

## Bootstrap determinista

```bash
python scripts/bootstrap_workspace.py --clean
```

Acciones:
- Crea/limpia `json/`, `texts/` y `uploads/` (carpetas ignoradas).
- Copia semillas reproducibles desde `data/seeds/` (`sample_topics.jsonl`, `sample_source.txt`).
- Deja `uploads/` listo para PDFs locales (añade un `README.bootstrap` explicativo).

Úsalo después de clonar, antes de correr `watcher_app.py` o al resetear datos locales.

## Higiene del repositorio

```bash
python scripts/check_repo_hygiene.py
```

Comprueba que no haya archivos prohibidos en el índice de Git (logs, dumps, credenciales). El mismo script corre en CI (`.github/workflows/hygiene.yml`) y fallará si se detecta algún artefacto.

Si aparecen incidencias, elimina los archivos afectados o vuelve a ejecutar el bootstrap con `--clean`.
