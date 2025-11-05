import os
import types
import builtins

import pytest


def test_firestore_hit_avoids_generation(monkeypatch):
    import embeddings_manager as em

    # Simular Firestore con un hit
    vec = [0.1, 0.2, 0.3]
    monkeypatch.setattr(em, "_firestore_load", lambda key, fp: vec)
    monkeypatch.setattr(em, "_firestore_store", lambda *a, **k: None)

    # Forzar proveedor no-Vertex para no depender de SDK externo
    monkeypatch.setenv("EMB_PROVIDER", "openrouter")

    out1 = em.get_embedding("hello world", force=False)
    assert out1 == vec

    # Segundo llamado debería resolver desde LRU sin invocar Firestore
    # Simular fallo en _firestore_load si fuese llamado
    monkeypatch.setattr(em, "_firestore_load", lambda key, fp: None)
    out2 = em.get_embedding("hello world", force=False)
    assert out2 == vec


def test_fingerprint_isolation(monkeypatch):
    import embeddings_manager as em

    # Habilitar capa Firestore para que _firestore_store se ejecute
    monkeypatch.setenv("EMB_USE_FIRESTORE", "1")
    # Re-import con env aplicado si el módulo cacheó el flag
    import importlib
    importlib.reload(em)

    # In-memory mock de Firestore por fingerprint
    store = {}
    def fs_get(key, fp):
        return store.get((fp, key))
    def fs_put(key, fp, vec, text, ttl_seconds=None):
        store[(fp, key)] = vec

    monkeypatch.setattr(em, "firestore_get_embedding", fs_get)
    monkeypatch.setattr(em, "firestore_put_embedding", fs_put)

    # Stub de AppSettings.load para cambiar embed_model
    class StubSettings:
        def __init__(self, embed_model):
            self.embed_model = embed_model
            self.openrouter_base_url = "https://openrouter.ai/api/v1"
            self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "test")

    seq = [StubSettings("modelA"), StubSettings("modelB")]
    def stub_load():
        # pop-like
        return seq[0] if len(seq) == 1 else seq.pop(0)

    # Patch directo del símbolo importado en embeddings_manager
    monkeypatch.setattr(em, "AppSettings", types.SimpleNamespace(load=stub_load))

    # Simular generación devolviendo vectores distintos por modelo
    def fake_http_call(model, text):
        return [1.0, 0.0] if model == "modelA" else [0.0, 1.0]
    monkeypatch.setattr(em, "_chroma_load", lambda *a, **k: None)
    monkeypatch.setattr(em, "_fs_load", lambda *a, **k: None)
    monkeypatch.setattr(em, "_lru_get", lambda *a, **k: None)
    monkeypatch.setattr(em, "_http_call", fake_http_call)
    monkeypatch.setattr(em, "_sdk_call", lambda model, text: None)

    # Primer modelo
    outA = em.get_embedding("same text", force=False)
    assert outA == [1.0, 0.0]
    # Segundo modelo (nuevo fingerprint)
    outB = em.get_embedding("same text", force=False)
    assert outB == [0.0, 1.0]
    # Verificar aislamiento en el store
    assert store
    assert any(fp == "modelA" for (fp, _key) in store.keys())
    assert any(fp == "modelB" for (fp, _key) in store.keys())
