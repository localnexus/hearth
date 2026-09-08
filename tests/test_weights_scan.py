"""test_weights_scan.py — the three layout readers, over synthetic roots.

Every fixture is a TINY but VALID GGUF written with gguf.GGUFWriter into a
TemporaryDirectory, so the readers are exercised against real headers and real
magic bytes without a gigabyte anywhere. No product is installed, running, or
present in any of these tests — which is guard rail R5 by construction. Pins:

  1. plain root: a two-shard model groups into ONE candidate whose size is the
     sum, and the mmproj beside it is classified and paired, not enrolled as a
     model of its own;
  2. an LM-Studio-shaped `publisher/repo/` tree reads as plain, with the tree
     path as the display key;
  3. Ollama: manifests resolve to blobs; a `.tensor`-only manifest (a
     safetensors import) is skipped; a manifest whose blob does not begin
     `GGUF` is skipped; the projector layer pairs to its model;
  4. the Hugging Face cache: `models--org--repo/snapshots/<rev>/x.gguf` is a
     symlink into blobs/ and is enrolled by its REAL path, under the display
     key `org/repo`;
  5. a `.internal/` directory is never descended into;
  6. two copies of the same bytes get the same identity and fill each other's
     `duplicates`;
  7. R2: a root reached through a symlink still yields real paths.

Run:  .venv/bin/python -m unittest tests.test_weights_scan
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
from gguf import GGUFWriter

from hearth.weights.roots import Root
from hearth.weights.scan import scan_all, scan_dir, scan_ollama, scan_root


def tiny_gguf(path: Path, arch: str = "qwen35moe", name: str = "tiny",
              blocks: int = 41, nextn: int = 1, kv_heads: int = 2,
              head_dim: int = 256, interval: int | None = 4,
              ctx: int = 262144, tensor: int = 4) -> Path:
    """A valid GGUF with the KV the fit arithmetic reads and one small tensor."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = GGUFWriter(str(path), arch)
    writer.add_name(name)
    writer.add_uint32(f"{arch}.block_count", blocks)
    writer.add_uint32(f"{arch}.context_length", ctx)
    writer.add_uint32(f"{arch}.attention.head_count", 16)
    writer.add_uint32(f"{arch}.attention.head_count_kv", kv_heads)
    writer.add_uint32(f"{arch}.attention.key_length", head_dim)
    writer.add_uint32(f"{arch}.attention.value_length", head_dim)
    if interval is not None:
        writer.add_uint32(f"{arch}.full_attention_interval", interval)
    writer.add_uint32(f"{arch}.nextn_predict_layers", nextn)
    writer.add_uint32(f"{arch}.expert_count", 256)
    writer.add_file_type(7)
    writer.add_tensor("blk.0.weight", np.zeros((tensor, tensor), dtype=np.float32))
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    return path


def manifest(path: Path, layers: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schemaVersion": 2, "layers": layers}),
                    encoding="utf-8")


class _Tree(unittest.TestCase):
    """One temporary directory holding one of each layout."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def root(self, path: Path, name="r", kind="user") -> Root:
        return Root(name, path, kind)

    def scan(self, path: Path, name="r") -> list:
        return scan_root(self.root(path, name), use_cache=False)


class PlainRoot(_Tree):

    def test_shards_group_and_the_projector_pairs(self):
        d = self.base / "models" / "publisher" / "repo"
        tiny_gguf(d / "big-00001-of-00002.gguf", tensor=4)
        tiny_gguf(d / "big-00002-of-00002.gguf", tensor=8)
        tiny_gguf(d / "mmproj-big-BF16.gguf", arch="clip", tensor=2)

        found = self.scan(self.base / "models")
        models = [c for c in found if c.kind == "model"]
        projectors = [c for c in found if c.kind == "mmproj"]
        self.assertEqual(len(models), 1, [c.display_key for c in found])
        self.assertEqual(len(projectors), 1)

        model = models[0]
        self.assertEqual(model.display_key, "publisher/repo/big")
        self.assertEqual(len(model.shards), 2)
        self.assertEqual(model.path, model.shards[0])
        self.assertEqual(model.size_bytes,
                         sum(s.stat().st_size for s in model.shards))
        self.assertEqual(model.layout, "plain")
        self.assertEqual(model.header.architecture, "qwen35moe")
        self.assertEqual([p.path for p in model.mmproj_candidates],
                         [projectors[0].path])

    def test_a_clip_header_is_a_projector_however_it_is_named(self):
        d = self.base / "m"
        tiny_gguf(d / "vision-tower.gguf", arch="clip")
        found = scan_dir(d, self.root(d), use_cache=False)
        self.assertEqual([c.kind for c in found], ["mmproj"])

    def test_a_single_unsharded_file_keeps_no_shard_list(self):
        d = self.base / "m"
        tiny_gguf(d / "solo.gguf")
        found = scan_dir(d, self.root(d), use_cache=False)
        self.assertEqual(found[0].shards, [])
        self.assertEqual(found[0].display_key, "solo")


class OllamaStore(_Tree):

    def _store(self) -> Path:
        store = self.base / "ollama"
        blobs = store / "blobs"
        blobs.mkdir(parents=True)

        model_blob = blobs / ("sha256-" + "1" * 64)
        tiny_gguf(model_blob)
        proj_blob = blobs / ("sha256-" + "2" * 64)
        tiny_gguf(proj_blob, arch="clip")
        # A blob that is NOT a GGUF: the magic check must refuse it.
        not_gguf = blobs / ("sha256-" + "3" * 64)
        not_gguf.write_bytes(b"NOPE" + b"\0" * 64)
        # A safetensors import: .tensor layers only, no .model layer.
        tensor_blob = blobs / ("sha256-" + "4" * 64)
        tensor_blob.write_bytes(b"\0" * 16)

        m = store / "manifests" / "registry.ollama.ai" / "library"
        manifest(m / "good" / "q8", [
            {"mediaType": "application/vnd.ollama.image.model",
             "digest": "sha256:" + "1" * 64},
            {"mediaType": "application/vnd.ollama.image.projector",
             "digest": "sha256:" + "2" * 64},
        ])
        manifest(m / "safetensors-import" / "latest", [
            {"mediaType": "application/vnd.ollama.image.tensor",
             "digest": "sha256:" + "4" * 64},
        ])
        manifest(m / "not-a-gguf" / "latest", [
            {"mediaType": "application/vnd.ollama.image.model",
             "digest": "sha256:" + "3" * 64},
        ])
        return store

    def test_manifests_resolve_to_blobs_and_the_unusable_ones_are_skipped(self):
        store = self._store()
        found = scan_ollama(store, self.root(store, "ollama", "ollama"),
                            use_cache=False)
        models = [c for c in found if c.kind == "model"]
        self.assertEqual([c.display_key for c in models], ["library/good:q8"])
        model = models[0]
        self.assertEqual(model.layout, "ollama")
        self.assertEqual(model.path.name, "sha256-" + "1" * 64)
        self.assertEqual(len(model.mmproj_candidates), 1)
        self.assertEqual(model.mmproj_candidates[0].kind, "mmproj")

    def test_the_walk_finds_the_store_and_stops_there(self):
        store = self._store()
        found = self.scan(self.base)
        self.assertEqual([c.display_key for c in found if c.kind == "model"],
                         ["library/good:q8"])
        # The blobs directory itself is never read as a plain folder of GGUFs.
        self.assertTrue(all(c.layout == "ollama" for c in found))
        self.assertTrue((store / "blobs").is_dir())


class HuggingFaceCache(_Tree):

    def test_snapshot_symlinks_are_enrolled_by_their_real_path(self):
        hub = self.base / "hub"
        repo = hub / "models--org--repo"
        blob = repo / "blobs" / "abcdef"
        tiny_gguf(blob)
        snap = repo / "snapshots" / "rev1"
        snap.mkdir(parents=True)
        link = snap / "model-Q8_0.gguf"
        link.symlink_to(os.path.relpath(blob, snap))

        found = self.scan(hub)
        self.assertEqual(len(found), 1, [c.display_key for c in found])
        cand = found[0]
        self.assertEqual(cand.layout, "hf")
        self.assertEqual(cand.display_key, "org/repo/model-Q8_0")
        self.assertEqual(cand.path, blob.resolve())
        self.assertFalse(cand.path.is_symlink())


class NeverRead(_Tree):

    def test_a_products_private_cache_directory_is_not_descended(self):
        root = self.base / "models"
        tiny_gguf(root / "kept.gguf")
        tiny_gguf(root / ".internal" / "hidden.gguf")
        tiny_gguf(root / ".cache" / "hidden.gguf")
        keys = {c.display_key for c in self.scan(root)}
        self.assertEqual(keys, {"kept"})


class Duplicates(_Tree):

    def test_the_same_bytes_in_two_places_find_each_other(self):
        root = self.base / "models"
        tiny_gguf(root / "a" / "same.gguf")
        (root / "b").mkdir(parents=True)
        (root / "b" / "same.gguf").write_bytes((root / "a" / "same.gguf").read_bytes())
        tiny_gguf(root / "c" / "other.gguf", name="different-name")

        found = scan_all([self.root(root)], use_cache=False)
        by_key = {c.display_key: c for c in found}
        first, second = by_key["a/same"], by_key["b/same"]
        self.assertEqual(first.identity, second.identity)
        self.assertEqual(first.duplicates, [second.path])
        self.assertEqual(second.duplicates, [first.path])
        self.assertEqual(by_key["c/other"].duplicates, [])


class RealPaths(_Tree):

    def test_a_root_reached_through_a_symlink_yields_real_paths(self):
        real = self.base / "real-models"
        tiny_gguf(real / "pub" / "repo" / "m.gguf")
        link = self.base / "linked-models"
        link.symlink_to(real)

        found = scan_all([Root("linked", link, "user")], use_cache=False)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].path, (real / "pub" / "repo" / "m.gguf").resolve())
        self.assertNotIn("linked-models", str(found[0].path))

    def test_the_same_file_under_two_roots_is_listed_once(self):
        real = self.base / "real-models"
        tiny_gguf(real / "m.gguf")
        link = self.base / "linked-models"
        link.symlink_to(real)
        found = scan_all([Root("real", real, "user"), Root("linked", link, "user")],
                         use_cache=False)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].root_name, "real")


if __name__ == "__main__":
    unittest.main()


class SymlinksLeavingTheRoots(_Tree):
    """The fence: a link out of every declared root is named, never followed —
    and never stat()ed through, because on a Mac that open can block forever
    on a permission prompt a daemon will not see."""

    def setUp(self):
        super().setUp()
        self.root_dir = self.base / "root"
        self.outside = self.base / "outside"
        (self.root_dir / "inside").mkdir(parents=True)
        self.outside.mkdir()
        tiny_gguf(self.root_dir / "inside" / "in.gguf", name="in")
        tiny_gguf(self.outside / "out.gguf", name="out")
        (self.root_dir / "away").symlink_to(self.outside)                    # dir link out
        (self.root_dir / "away.gguf").symlink_to(self.outside / "out.gguf")  # file link out
        (self.root_dir / "near").symlink_to(self.root_dir / "inside")        # dir link in

    def test_links_out_are_reported_and_not_followed(self):
        found = scan_all([Root("r", self.root_dir, "user")], use_cache=False)
        models = [c for c in found if c.kind == "model"]
        elsewhere = [c for c in found if c.kind == "elsewhere"]
        self.assertEqual(sorted(c.display_key for c in models), ["inside/in"])  # near/ = same file, deduped
        self.assertEqual(sorted(c.display_key for c in elsewhere), ["away", "away.gguf"])
        for c in elsewhere:
            self.assertIn(str(self.outside.resolve()), c.header_error)
            self.assertEqual(c.size_bytes, 0)
            self.assertEqual(c.identity, "")
            self.assertEqual(c.duplicates, [])
        self.assertFalse(any("out" in c.display_key for c in models))

    def test_a_link_into_another_declared_root_is_followed(self):
        roots = [Root("r", self.root_dir, "user"), Root("o", self.outside, "user")]
        found = scan_all(roots, use_cache=False)
        self.assertEqual([c for c in found if c.kind == "elsewhere"], [])
        # the outside file is reached three ways (away.gguf, away/out.gguf,
        # o's own walk) and kept once, by real path
        paths = [c.path for c in found if c.kind == "model"]
        self.assertEqual(paths.count((self.outside / "out.gguf").resolve()), 1, paths)

    def test_a_lone_root_fences_on_itself(self):
        found = scan_root(Root("r", self.root_dir, "user"), use_cache=False)
        self.assertEqual(sorted(c.display_key for c in found if c.kind == "elsewhere"),
                         ["away", "away.gguf"])
