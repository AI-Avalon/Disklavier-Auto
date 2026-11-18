"""
RTX 3070 (CUDA) Classical MIDI Corpus Phrase Transformer Training Pipeline.

Scans `data/imslp_midi`, `data/human_references`, `data/maestro-v3.0.0`, and `data/midi`
using `symusic`, extracts idiomatic piano phrasing (cantabile melody + post-leap gap-fill,
breathing LH accompaniments, suspensions, agogic rubato & velocity arches), trains
`ClassicalPhraseTransformerNet` on NVIDIA GeForce RTX 3070 CUDA, and saves:
  - `data/models/classical_phrase_model.pt`
  - `data/models/classical_phrase_model_meta.json`
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any, Dict, List

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.stage.corpus_phrase_model import (
    DEFAULT_META_PATH,
    DEFAULT_MODEL_PATH,
    ClassicalPhraseTransformerNet,
    scan_and_extract_corpus_dataset,
)


def train_phrase_model(
    max_files: int = 180,
    max_windows_per_file: int = 12,
    epochs: int = 20,
    batch_size: int = 64,
    lr: float = 1.8e-3,
    out_model_path: Path = DEFAULT_MODEL_PATH,
    out_meta_path: Path = DEFAULT_META_PATH,
) -> Dict[str, Any]:
    t_start = time.perf_counter()
    samples, corpus_stats = scan_and_extract_corpus_dataset(
        max_files=max_files,
        max_windows_per_file=max_windows_per_file,
    )
    if len(samples) < 16:
        raise RuntimeError(f"Insufficient MIDI phrase samples extracted from data/: {len(samples)}")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    gpu_name = torch.cuda.get_device_name(0) if device == "cuda" else "CPU"

    cond_arr = torch.from_numpy(np.stack([s["cond"] for s in samples])).float()
    deg_arr = torch.from_numpy(np.stack([s["deg_classes"] for s in samples])).long()
    lh_arr = torch.tensor([s["lh_idiom"] for s in samples], dtype=torch.long)
    susp_arr = torch.tensor([s["suspension"] for s in samples], dtype=torch.long)
    agogic_arr = torch.from_numpy(np.stack([s["agogic"] for s in samples])).float()
    vel_arr = torch.from_numpy(np.stack([s["vel_curve"] for s in samples])).float()

    dataset = TensorDataset(cond_arr, deg_arr, lh_arr, susp_arr, agogic_arr, vel_arr)
    loader = DataLoader(dataset, batch_size=min(batch_size, len(samples)), shuffle=True, drop_last=False)

    model = ClassicalPhraseTransformerNet().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(1, epochs))
    ce_loss = nn.CrossEntropyLoss()
    mse_loss = nn.MSELoss()

    loss_history: List[float] = []
    model.train()
    for ep in range(epochs):
        total_loss = 0.0
        n_batches = 0
        for b_cond, b_deg, b_lh, b_susp, b_ag, b_vel in loader:
            b_cond = b_cond.to(device)
            b_deg = b_deg.to(device)
            b_lh = b_lh.to(device)
            b_susp = b_susp.to(device)
            b_ag = b_ag.to(device)
            b_vel = b_vel.to(device)

            optimizer.zero_grad(set_to_none=True)
            out = model(b_cond)

            loss_deg = ce_loss(out["melody_logits"].reshape(-1, out["melody_logits"].shape[-1]), b_deg.reshape(-1))
            loss_lh = ce_loss(out["lh_logits"], b_lh)
            loss_susp = ce_loss(out["susp_logits"], b_susp)
            loss_ag = mse_loss(out["agogic"], b_ag)
            loss_vel = mse_loss(out["vel_norm"], b_vel)

            loss = loss_deg + 0.65 * loss_lh + 0.45 * loss_susp + 0.8 * loss_ag + 0.8 * loss_vel
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            total_loss += float(loss.item())
            n_batches += 1
        scheduler.step()
        ep_loss = round(total_loss / max(1, n_batches), 5)
        loss_history.append(ep_loss)

    model.eval()
    # Measure single-inference latency on device
    probe = cond_arr[:1].to(device)
    if device == "cuda":
        torch.cuda.synchronize()
    t_inf0 = time.perf_counter()
    with torch.no_grad():
        _ = model(probe)
    if device == "cuda":
        torch.cuda.synchronize()
    inf_ms = round((time.perf_counter() - t_inf0) * 1000.0, 3)

    elapsed_s = round(time.perf_counter() - t_start, 2)
    meta: Dict[str, Any] = {
        **corpus_stats,
        "device": device,
        "gpu_name": gpu_name,
        "epochs": epochs,
        "initial_loss": loss_history[0],
        "final_loss": loss_history[-1],
        "loss_history": loss_history,
        "inference_latency_ms": inf_ms,
        "training_elapsed_s": elapsed_s,
    }

    out_model_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "corpus_stats": meta,
        },
        str(out_model_path),
    )
    out_meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description="Train Classical Phrase Transformer on RTX 3070 CUDA")
    parser.add_argument("--max-files", type=int, default=180)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()

    meta = train_phrase_model(
        max_files=args.max_files,
        epochs=args.epochs,
        batch_size=args.batch_size,
    )
    print(
        f"[TRAIN PASS] Device={meta['device']} ({meta['gpu_name']}) | "
        f"Files={meta['files_parsed']}/{meta['files_scanned']} | Samples={meta['total_phrase_samples']} | "
        f"Loss {meta['initial_loss']:.4f} -> {meta['final_loss']:.4f} | "
        f"Infer={meta['inference_latency_ms']}ms | GapFill={meta['mean_post_leap_gap_fill_rate']:.2%}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
