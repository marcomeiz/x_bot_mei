# 01 · Arquitectura (vista 10.000 ft)

La arquitectura define flujos e interacciones, no un listado de ficheros.

```mermaid
graph TD
    subgraph Ingesta [Pipeline de Ingesta]
        direction LR
        U(Uploads/PDFs) --> W(Watcher v2)
        R(Reddit/HF) --> I(Ingestion)
        W --> PE(pdf_extractor) --> TP(topic_pipeline)
        I --> TP
        TP --> PS(persistence_service)
    end

    subgraph Core [Generación y Persistencia]
        direction TB
        PS --> C(ChromaDB_Topics)
        CG(core_generator) --> VG(variant_generators)
        VG --> LLM(llm_fallback)
        PS -- Notifica --> TS(telegram_client)
    end

    subgraph Bot [Interfaz de Usuario]
        direction TD
        U_TG[Usuario Telegram] -- /g,/c --> B(bot)
        B --> PR(proposal_service)
        PR --> U_TG
        U_TG -- Callback --> PR
        PR --> DR(draft_repository)
        PR -- Aprueba --> MEM(ChromaDB_Memory)
    end

    Ingesta --> Core
    Core --> Bot
```

## Componentes
- Watcher v2 / run_watcher.py: observa `uploads/` y dispara extracción de temas.
- topic_pipeline: extracción/validación de temas; gating de estilo.
- persistence_service: inserta en Chroma y sincroniza remoto.
- core_generator: orquesta selección de tema y generación A/B/C.
- variant_generators: prompts y validadores de estilo/longitud.
- llm_fallback: capa LLM con JSON estricto y fallback entre proveedores.
- proposal_service: bot de propuestas, callbacks y memoria.
- telegram_client: envío seguro a Telegram (HTML, teclados, fallback).

