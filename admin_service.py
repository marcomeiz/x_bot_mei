import html
from typing import Dict, List, Optional, Tuple

from embeddings_manager import get_embedding, get_topics_collection, get_memory_collection
from logger_config import logger
from src.chroma_utils import flatten_chroma_metadatas, get_existing_ids


class AdminService:
    """Encapsulates admin utilities consumed by HTTP routes and commands."""

    def __init__(self, batch_size: int = 500) -> None:
        self.batch_size = batch_size

    # --------------------------------------------------------------------- stats
    def get_stats(self) -> Dict[str, Optional[int]]:
        try:
            topics_count = get_topics_collection().count()  # type: ignore[arg-type]
        except Exception:
            topics_count = None
        try:
            memory_count = get_memory_collection().count()  # type: ignore[arg-type]
        except Exception:
            memory_count = None
        return {"topics": topics_count, "memory": memory_count}

    # ---------------------------------------------------------------- pdf stats
    def collect_pdf_stats(self) -> Dict[str, object]:
        try:
            coll = get_topics_collection()
            # Si la colección está vacía, count() es 0 y podemos salir rápido.
            total_topics = coll.count()
            if total_topics == 0:
                return {"distinct_pdfs": 0, "total_topics": 0, "pdf_counts": {}}

            # Traemos todos los metadatos de una vez.
            data = coll.get(include=["metadatas"])
            
            metadatas = flatten_chroma_metadatas(data.get("metadatas"))
            if not metadatas:
                return {"distinct_pdfs": 0, "total_topics": total_topics, "pdf_counts": {}}

            pdf_counts: Dict[str, int] = {}
            for md in metadatas:
                pdf_name = md.get("source_pdf") if isinstance(md, dict) else None
                key = pdf_name if pdf_name else "_unknown"
                pdf_counts[key] = pdf_counts.get(key, 0) + 1
            
            distinct = len([k for k in pdf_counts.keys() if k != "_unknown"])
            return {"distinct_pdfs": distinct, "total_topics": total_topics, "pdf_counts": pdf_counts}
        except Exception as exc:
            logger.error("collect_pdf_stats failed: %s", exc, exc_info=True)
            raise

    @staticmethod
    def build_pdf_summary_message(stats: Dict[str, object], limit: int = 20) -> str:
        lines = ["<b>PDFs ingeridos</b>"]
        lines.append(f"Distinct: {stats.get('distinct_pdfs', 0)}")
        lines.append(f"Total topics: {stats.get('total_topics', 0)}")

        pdf_counts = stats.get("pdf_counts") or {}
        if pdf_counts:
            lines.append("")
            sorted_counts = sorted(pdf_counts.items(), key=lambda kv: kv[1], reverse=True)
            extras = 0
            if len(sorted_counts) > limit:
                extras = len(sorted_counts) - limit
                sorted_counts = sorted_counts[:limit]
            for name, count in sorted_counts:
                safe_name = html.escape(str(name))
                lines.append(f"• {safe_name} · {count}")
            if extras:
                lines.append(html.escape(f"… y {extras} más"))
        return "\n".join(lines)

    # --------------------------------------------------------------- ingestion
    def ingest_topics(self, items: List[Dict[str, object]]) -> Tuple[int, int, int]:
        normalized: List[Tuple[str, str, Dict[str, str]]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("id") or "").strip()
            abstract = str(item.get("abstract") or "").strip()
            pdf = str(item.get("pdf") or item.get("source_pdf") or "").strip() or None
            if not tid or not abstract:
                continue
            # Chroma requiere metadatos no vacíos; si no hay PDF, establecemos una fuente genérica
            md = {"pdf": pdf} if pdf else {"source": "topics"}
            normalized.append((tid, abstract, md))

        if not normalized:
            return 0, 0, 0

        coll = get_topics_collection()
        ids = [tid for tid, _, _ in normalized]
        existing_ids = get_existing_ids(coll, ids, log_warning=logger.warning)
        to_add = [(tid, abs_, md) for tid, abs_, md in normalized if tid not in existing_ids]

        added = 0
        skipped_existing = len(normalized) - len(to_add)
        errors = 0

        if to_add:
            embeddings = []
            docs = []
            tids = []
            metadatas = []
            for tid, abs_, md in to_add:
                embedding = get_embedding(abs_)
                if embedding is None:
                    errors += 1
                    continue
                embeddings.append(embedding)
                docs.append(abs_)
                tids.append(tid)
                metadatas.append(md)
            if embeddings:
                coll.add(embeddings=embeddings, documents=docs, ids=tids, metadatas=metadatas)
                added = len(embeddings)
        return added, skipped_existing, errors
