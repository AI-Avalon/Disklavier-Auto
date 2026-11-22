(() => {
  "use strict";

  const LIVE_MODE = Boolean(window.DISKLAVIER_LIVE_MODE);

  const STYLE_LABELS = {
    broken_chord_barcarolle: "分散和音・バルカローレ",
    cantabile_polyphony: "カンタービレ多声対位",
    impressionist_pedal: "印象派・半ペダル共鳴",
    dramatic_octave_chorale: "重厚コラール・オクターヴ",
    minimalist_pulse: "静謐反復アルペジオ",
    countermelody_tenor: "内声テノール対旋律",
  };

  const MODE_LABELS = {
    cello: "チェロ対話",
    piano: "ピアノ対話",
    notagen: "NotaGen ライブ",
    library: "楽曲ライブラリ",
  };

  const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

  const PC_KEY_TO_MIDI = {
    KeyA: 60,
    KeyW: 61,
    KeyS: 62,
    KeyE: 63,
    KeyD: 64,
    KeyF: 65,
    KeyT: 66,
    KeyG: 67,
    KeyY: 68,
    KeyH: 69,
    KeyU: 70,
    KeyJ: 71,
    KeyK: 72,
  };

  function midiToName(midi) {
    const m = Math.max(21, Math.min(108, Math.round(Number(midi) || 60)));
    return `${NOTE_NAMES[m % 12]}${Math.floor(m / 12) - 1}`;
  }

  function midiToFreq(midi) {
    return 440 * Math.pow(2, (midi - 69) / 12);
  }

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  function formatTime(sec) {
    const s = Math.max(0, Number(sec) || 0);
    const mins = Math.floor(s / 60);
    const rem = s - mins * 60;
    return `${mins}:${rem.toFixed(1).padStart(4, "0")}`;
  }

  function normNote(raw) {
    if (!raw || typeof raw !== "object") return null;
    const p = Number(raw.pitch ?? raw.p ?? 60);
    const t = Number(raw.onset_s ?? raw.t ?? 0);
    const d = Math.max(0.08, Number(raw.duration_s ?? raw.d ?? 0.45));
    const v = clamp(Number(raw.velocity ?? raw.v ?? 64), 1, 127);
    const h = String(raw.hand ?? raw.h ?? (p >= 60 ? "rh" : "lh"));
    const r = String(raw.role ?? raw.r ?? "");
    return { p, t, d, v, h, r };
  }

  // ===========================================================================
  // WebAudio Polyphonic Acoustic Piano & Cello Synthesizer (with Hall Reverb)
  // ===========================================================================
  class HallWebAudioEngine {
    constructor() {
      this.ctx = null;
      this.masterGain = null;
      this.dryGain = null;
      this.wetGain = null;
      this.convolver = null;
      this.activeVoices = new Set();
      this.muted = false;
      this.lastLevelDb = -60;
    }

    ensure() {
      if (this.ctx) {
        if (this.ctx.state === "suspended") {
          this.ctx.resume().catch(() => {});
        }
        return this.ctx;
      }
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      const ctx = new AC();
      const master = ctx.createGain();
      master.gain.value = this.muted ? 0 : 0.72;

      const compressor = ctx.createDynamicsCompressor();
      compressor.threshold.value = -14;
      compressor.ratio.value = 3.2;

      const dry = ctx.createGain();
      dry.gain.value = 0.84;

      const wet = ctx.createGain();
      wet.gain.value = 0.24;

      const convolver = ctx.createConvolver();
      convolver.buffer = this._makeHallImpulse(ctx, 1.9, 2.6);

      dry.connect(compressor);
      wet.connect(convolver);
      convolver.connect(compressor);
      compressor.connect(master);
      master.connect(ctx.destination);

      this.ctx = ctx;
      this.masterGain = master;
      this.dryGain = dry;
      this.wetGain = wet;
      this.convolver = convolver;
      return ctx;
    }

    _makeHallImpulse(ctx, seconds, decay) {
      const len = Math.floor(ctx.sampleRate * seconds);
      const buf = ctx.createBuffer(2, len, ctx.sampleRate);
      for (let ch = 0; ch < 2; ch++) {
        const data = buf.getChannelData(ch);
        for (let i = 0; i < len; i++) {
          const env = Math.pow(1 - i / len, decay);
          data[i] = (Math.random() * 2 - 1) * env * 0.45;
        }
      }
      return buf;
    }

    setMuted(flag) {
      this.muted = Boolean(flag);
      if (this.masterGain && this.ctx) {
        this.masterGain.gain.setTargetAtTime(this.muted ? 0 : 0.72, this.ctx.currentTime, 0.02);
      }
    }

    playNote(midi, velocity = 64, durationS = 0.65, options = {}) {
      if (this.muted) return;
      const ctx = this.ensure();
      if (!ctx) return;

      const when = ctx.currentTime + Math.max(0, options.delayS || 0);
      const isHuman = Boolean(options.human);
      const unaCorda = Number(options.cc67 || 0) >= 40;
      const damperBoost = Number(options.cc64 || 0) >= 48 ? 0.55 : 0.08;
      const freq = midiToFreq(midi);
      const amp = clamp((velocity / 127) * (isHuman ? 0.22 : 0.16), 0.015, 0.24);
      const dur = clamp(durationS + damperBoost, 0.12, 3.6);

      const env = ctx.createGain();
      env.gain.setValueAtTime(0.0001, when);
      env.gain.linearRampToValueAtTime(amp, when + (isHuman ? 0.028 : 0.008));
      env.gain.exponentialRampToValueAtTime(Math.max(0.0008, amp * 0.38), when + Math.min(dur * 0.45, 0.55));
      env.gain.exponentialRampToValueAtTime(0.0001, when + dur);

      const filter = ctx.createBiquadFilter();
      filter.type = "lowpass";
      const cutoff = unaCorda ? Math.min(3400, freq * 3.1) : Math.min(7200, freq * 5.8 + velocity * 18);
      filter.frequency.setValueAtTime(cutoff, when);
      filter.frequency.exponentialRampToValueAtTime(Math.max(320, cutoff * 0.42), when + dur);

      const osc1 = ctx.createOscillator();
      osc1.type = isHuman ? "sawtooth" : "triangle";
      osc1.frequency.setValueAtTime(freq, when);

      const osc2 = ctx.createOscillator();
      osc2.type = "sine";
      osc2.frequency.setValueAtTime(freq * 2, when);

      const g2 = ctx.createGain();
      g2.gain.value = unaCorda ? 0.12 : 0.28;

      osc1.connect(filter);
      osc2.connect(g2);
      g2.connect(filter);
      filter.connect(env);
      env.connect(this.dryGain);
      env.connect(this.wetGain);

      const handle = { osc1, osc2, env };
      this.activeVoices.add(handle);
      this.lastLevelDb = Math.max(this.lastLevelDb, -36 + (velocity / 127) * 28);

      osc1.start(when);
      osc2.start(when);
      osc1.stop(when + dur + 0.04);
      osc2.stop(when + dur + 0.04);
      osc1.onended = () => {
        this.activeVoices.delete(handle);
        try {
          env.disconnect();
        } catch (_) {}
      };
    }

    stopAll() {
      if (!this.ctx) return;
      const now = this.ctx.currentTime;
      for (const v of this.activeVoices) {
        try {
          v.env.gain.cancelScheduledValues(now);
          v.env.gain.setTargetAtTime(0.0001, now, 0.015);
          v.osc1.stop(now + 0.06);
          v.osc2.stop(now + 0.06);
        } catch (_) {}
      }
      this.activeVoices.clear();
    }
  }

  const audio = new HallWebAudioEngine();

  // ===========================================================================
  // State
  // ===========================================================================
  const state = {
    activeView: LIVE_MODE ? "cello" : "library",
    connProduction: false,
    monitorAudio: true,
    diagVisible: true,
    projectionClean: false,
    stage: null,
    ws: null,
    wsConnected: false,
    inspectedCand: {
      cello: null,
      piano: null,
    },
    lastRenderedTurnId: -1,
    auditionTimer: null,
    browserHumanKeys: new Map(), // pitch -> expiryTs
    notagen: {
      status: "idle",
      active_backend: "auto",
      bars_completed: 0,
      target_bars: 8,
      ttft_ms: 0,
      abc_text: "",
      notes: [],
      commentary_ja: "",
      guard_summary: "待機中",
      pedal_cc64: 0,
      pedal_cc67: 0,
      key_sig: "Dm",
      playedBars: 0,
      simTimer: null,
    },
    // Mode 4 Library State
    catalog: null,
    filteredPieces: [],
    activeFilter: "all",
    activeStyle: "all",
    searchText: "",
    selectedPieceMeta: null,
    pieceData: null,
    libPlaying: false,
    libStartPerfMs: 0,
    libCursorSec: 0,
    libTriggeredIdx: 0,
    libRafId: null,
    layers: {
      human: { mute: false, solo: false },
      melody: { mute: false, solo: false },
      harmony: { mute: false, solo: false },
      bass: { mute: false, solo: false },
    },
  };

  // Offline / Static Demo Fallback Snapshot so Modes 1 & 2 look meaningful even offline
  function createDefaultOfflineStage(mode = "cello") {
    return {
      mode,
      production_mode: false,
      demo_enabled: false,
      monitor_audio: true,
      live_listening: true,
      human_active_pitches: [],
      active_preset: "晩年ロマン派・静的Cantabile",
      turn_id: 1,
      telemetry_hud: {
        parallel_compose_ms: 31.4,
        llm_ttft_ms: 282.0,
        speculative_hits: 3,
        speculative_misses: 0,
        l5_jitter_ms: 1.4,
        active_workers: 6,
        total_workers: 6,
        active_voices: 4,
        hard_limit: 30,
        vram_used_gb: 3.1,
        vram_total_gb: 8.0,
      },
      perception: {
        f0_hz: 146.83,
        note_name: "D3",
        articulation: "Cantabile",
        vibrato_cents: 14,
        nlms_erle_db: 15.2,
        breath_gauge: 0.68,
        turn_ready: true,
        echo_suppressed_count: 0,
        pedal_cc64: 72,
        pedal_cc67: 48,
      },
      intent: {
        act_number: 1,
        tonal_center: "Dm",
        chord_progression: ["Dm9", "Gm6/Bb", "A7sus4", "Dm"],
        monologue_ja:
          "D短調の主題を受け、左手A2-E3の5度持続と右手F4-A4の穏やかな3和音で余白を残して応答する。",
      },
      output: {
        active_pitches: [50, 57, 65, 69],
        active_voices: 4,
        hard_limit: 30,
        pedal_cc64: 72,
        pedal_cc67: 48,
      },
      candidates: [
        {
          candidate_id: "A",
          label_ja: "案A — 分散和音・4声対話",
          total_score: 0.942,
          adopted: true,
          pitch_range_str: "A2–A5",
          beauty_summary: "採用: 両手声部連結が滑らかでNLMS分離後の帯域と調和 (94.2%)",
          rejection_tag_ja: "採用",
          events: [
            { pitch: 50, onset_s: 0.0, duration_s: 1.4, velocity: 52, hand: "human" },
            { pitch: 53, onset_s: 0.65, duration_s: 1.2, velocity: 54, hand: "human" },
            { pitch: 57, onset_s: 1.3, duration_s: 1.4, velocity: 56, hand: "human" },
            { pitch: 38, onset_s: 0.1, duration_s: 2.4, velocity: 46, hand: "lh" },
            { pitch: 45, onset_s: 0.55, duration_s: 1.9, velocity: 44, hand: "lh" },
            { pitch: 65, onset_s: 0.15, duration_s: 1.3, velocity: 56, hand: "rh" },
            { pitch: 69, onset_s: 0.9, duration_s: 1.4, velocity: 60, hand: "rh" },
            { pitch: 72, onset_s: 1.7, duration_s: 1.4, velocity: 58, hand: "rh" },
            { pitch: 74, onset_s: 2.4, duration_s: 1.5, velocity: 54, hand: "rh" },
          ],
        },
        {
          candidate_id: "B",
          label_ja: "案B — 密集テノール内声",
          total_score: 0.748,
          adopted: false,
          pitch_range_str: "C3–E4",
          rejection_tag_ja: "棄却: チェロ中音帯域と短2度衝突 (-19.4点)",
          events: [
            { pitch: 50, onset_s: 0.0, duration_s: 1.1, velocity: 52, hand: "human" },
            { pitch: 48, onset_s: 0.2, duration_s: 1.1, velocity: 50, hand: "lh" },
            { pitch: 52, onset_s: 0.25, duration_s: 1.1, velocity: 52, hand: "lh" },
            { pitch: 55, onset_s: 0.8, duration_s: 1.0, velocity: 54, hand: "rh" },
          ],
        },
        {
          candidate_id: "C",
          label_ja: "案C — 高域アルペジオ",
          total_score: 0.704,
          adopted: false,
          pitch_range_str: "C5–G6",
          rejection_tag_ja: "棄却: 左手バスの支え不足により和声安定度低下 (-23.8点)",
          events: [
            { pitch: 72, onset_s: 0.0, duration_s: 0.6, velocity: 54, hand: "rh" },
            { pitch: 76, onset_s: 0.45, duration_s: 0.6, velocity: 56, hand: "rh" },
            { pitch: 79, onset_s: 0.9, duration_s: 0.7, velocity: 58, hand: "rh" },
            { pitch: 84, onset_s: 1.35, duration_s: 0.9, velocity: 55, hand: "rh" },
          ],
        },
        {
          candidate_id: "D",
          label_ja: "案D — 順次音階進行",
          total_score: 0.562,
          adopted: false,
          pitch_range_str: "D4–A4",
          rejection_tag_ja: "棄却: 動機跳躍の回復がなく単音律動に偏向 (-38.0点)",
          events: [
            { pitch: 62, onset_s: 0.0, duration_s: 0.4, velocity: 50, hand: "rh" },
            { pitch: 64, onset_s: 0.4, duration_s: 0.4, velocity: 50, hand: "rh" },
            { pitch: 65, onset_s: 0.8, duration_s: 0.4, velocity: 50, hand: "rh" },
            { pitch: 67, onset_s: 1.2, duration_s: 0.4, velocity: 50, hand: "rh" },
          ],
        },
      ],
    };
  }

  state.stage = createDefaultOfflineStage(state.activeView === "piano" ? "piano" : "cello");

  // ===========================================================================
  // Helpers for API & Safe DOM access
  // ===========================================================================
  const $ = (id) => document.getElementById(id);

  async function postJson(url, body = {}) {
    if (!LIVE_MODE) return null;
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) return null;
      return await res.json();
    } catch (_) {
      return null;
    }
  }

  async function fetchJson(url) {
    try {
      const res = await fetch(url);
      if (!res.ok) return null;
      return await res.json();
    } catch (_) {
      return null;
    }
  }

  function showExportToast(elId, data) {
    const el = $(elId);
    if (!el) return;
    if (!data || data.error) {
      el.textContent = data?.error ? `保存エラー: ${data.error}` : "保存が完了しました";
      el.hidden = false;
      return;
    }
    const assets = data.assets || data.files || data;
    const parts = [];
    const mp3 = assets.mp3_url || assets.mp3;
    const wav = assets.wav_url || assets.wav;
    const mid = assets.mid_url || assets.mid || assets.midi;
    const xml = assets.musicxml_url || assets.musicxml;
    if (mp3) parts.push(`<a href="${mp3}" target="_blank" rel="noopener">MP3</a>`);
    if (wav) parts.push(`<a href="${wav}" target="_blank" rel="noopener">WAV</a>`);
    if (mid) parts.push(`<a href="${mid}" target="_blank" rel="noopener">MIDI</a>`);
    if (xml) parts.push(`<a href="${xml}" target="_blank" rel="noopener">MusicXML</a>`);
    if (data.obsidian_note || assets.obsidian) parts.push(`<span>Obsidianノート保存済</span>`);
    el.innerHTML = `<strong>保存完了:</strong> ${parts.length ? parts.join(" · ") : "ファイル書き出し完了"} <button class="btn compact" type="button" id="${elId}-close">閉じる</button>`;
    el.hidden = false;
    const btn = $(`${elId}-close`);
    if (btn) btn.onclick = () => { el.hidden = true; };
  }

  // ===========================================================================
  // Canvas Engraving Renderer: Hero Grand Staff & Mode 4 Timeline Score
  // ===========================================================================
  function pitchToDiatonicStep(midi) {
    const stepInOct = [0, 0, 1, 1, 2, 3, 3, 4, 4, 5, 5, 6];
    const isSharp = [false, true, false, true, false, false, true, false, true, false, true, false];
    const m = Math.round(midi);
    const oct = Math.floor(m / 12) - 1;
    const pc = ((m % 12) + 12) % 12;
    return { diatonic: oct * 7 + stepInOct[pc], sharp: isSharp[pc] };
  }

  function drawTrebleClef(ctx, x, bottomLineY, stepPx) {
    ctx.save();
    ctx.strokeStyle = "#8C857A";
    ctx.fillStyle = "#8C857A";
    ctx.lineWidth = 1.5;
    const gLineY = bottomLineY - 2 * stepPx; // G4 = 2nd line from bottom (treble)
    ctx.beginPath();
    ctx.arc(x, gLineY, stepPx * 1.45, 0, Math.PI * 1.65);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x + stepPx * 0.4, bottomLineY + stepPx * 1.8);
    ctx.bezierCurveTo(
      x + stepPx * 1.6,
      bottomLineY - stepPx * 9.5,
      x - stepPx * 1.4,
      bottomLineY - stepPx * 9.8,
      x + stepPx * 0.1,
      bottomLineY - stepPx * 3.8
    );
    ctx.stroke();
    ctx.font = `600 ${Math.max(13, Math.round(stepPx * 2.2))}px serif`;
    ctx.fillText("G", x - stepPx * 2.3, gLineY + stepPx * 0.7);
    ctx.restore();
  }

  function drawBassClef(ctx, x, bottomLineY, stepPx) {
    ctx.save();
    ctx.strokeStyle = "#8C857A";
    ctx.fillStyle = "#8C857A";
    ctx.lineWidth = 1.6;
    const fLineY = bottomLineY - 6 * stepPx; // F3 = 4th line from bottom (bass)
    ctx.beginPath();
    ctx.arc(x - stepPx * 0.3, fLineY, stepPx * 0.75, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x - stepPx * 0.1, fLineY + stepPx * 0.8, stepPx * 2.1, -Math.PI * 0.62, Math.PI * 0.42);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(x + stepPx * 2.4, fLineY - stepPx * 0.7, 2.1, 0, Math.PI * 2);
    ctx.arc(x + stepPx * 2.4, fLineY + stepPx * 0.9, 2.1, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  // Offscreen cache for static grand staff lines & clefs (60fps optimization)
  const _staffBgCache = new WeakMap();

  function _getCachedStaffBg(canvas, width, height, dpr, numBars, bgHex) {
    const key = `${width}x${height}x${dpr}x${numBars}x${bgHex}`;
    const existing = _staffBgCache.get(canvas);
    if (existing && existing.key === key) {
      return existing.offscreen;
    }
    const off = document.createElement("canvas");
    off.width = Math.floor(width * dpr);
    off.height = Math.floor(height * dpr);
    const octx = off.getContext("2d");
    octx.setTransform(dpr, 0, 0, dpr, 0, 0);

    octx.fillStyle = bgHex;
    octx.fillRect(0, 0, width, height);

    const padLeft = 58;
    const padRight = 22;
    const usableW = Math.max(80, width - padLeft - padRight);
    const trebleBaseY = Math.round(height * 0.46);
    const bassBaseY = Math.round(height * 0.85);
    const stepPx = Math.max(4.4, Math.min(6.0, height / 50));

    octx.strokeStyle = "#484B53";
    octx.lineWidth = 1;
    for (let i = 0; i < 5; i++) {
      const yt = trebleBaseY - i * 2 * stepPx;
      octx.beginPath();
      octx.moveTo(padLeft - 14, yt);
      octx.lineTo(width - padRight, yt);
      octx.stroke();

      const yb = bassBaseY - i * 2 * stepPx;
      octx.beginPath();
      octx.moveTo(padLeft - 14, yb);
      octx.lineTo(width - padRight, yb);
      octx.stroke();
    }

    octx.strokeStyle = "#6C707A";
    octx.lineWidth = 2;
    octx.beginPath();
    octx.moveTo(padLeft - 14, trebleBaseY - 8 * stepPx);
    octx.lineTo(padLeft - 14, bassBaseY);
    octx.stroke();

    drawTrebleClef(octx, 26, trebleBaseY, stepPx);
    drawBassClef(octx, 26, bassBaseY, stepPx);

    octx.strokeStyle = "#34373E";
    octx.lineWidth = 1;
    for (let b = 1; b <= numBars; b++) {
      const bx = padLeft + (b / numBars) * usableW;
      octx.beginPath();
      octx.moveTo(bx, trebleBaseY - 8 * stepPx);
      octx.lineTo(bx, bassBaseY);
      octx.stroke();
    }

    _staffBgCache.set(canvas, { key, offscreen: off });
    return off;
  }

  function renderGrandStaff(canvas, rawNotes, opts = {}) {
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    if (rect.width < 20 || rect.height < 20) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.floor(rect.width * dpr);
    const h = Math.floor(rect.height * dpr);
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }

    const width = rect.width;
    const height = rect.height;
    const padLeft = 58;
    const padRight = 22;
    const usableW = Math.max(80, width - padLeft - padRight);
    const trebleBaseY = Math.round(height * 0.46); // E4 line
    const bassBaseY = Math.round(height * 0.85); // G2 line
    const stepPx = Math.max(4.4, Math.min(6.0, height / 50));

    const notes = (rawNotes || []).map(normNote).filter(Boolean);
    let minT = opts.winStart ?? 0;
    let maxT = opts.winEnd ?? 4.0;
    if (opts.winStart == null && notes.length > 0) {
      minT = Math.min(...notes.map((n) => n.t));
      maxT = Math.max(minT + 3.2, ...notes.map((n) => n.t + n.d));
    }
    const span = Math.max(1.2, maxT - minT);
    const numBars = Math.max(2, Math.min(8, Math.round(span / 1.5)));
    const bgHex = state.projectionClean ? "#141517" : "#1C1D20";

    const ctx = canvas.getContext("2d");
    const cachedBg = _getCachedStaffBg(canvas, width, height, dpr, numBars, bgHex);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(cachedBg, 0, 0);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // Optional chords along top
    if (Array.isArray(opts.chords) && opts.chords.length > 0) {
      ctx.font = '600 13.5px "Cascadia Mono", monospace';
      ctx.fillStyle = "#B4AEA3";
      opts.chords.forEach((ch, idx) => {
        const cx =
          ch.start_s != null
            ? padLeft + ((ch.start_s - minT) / span) * usableW
            : padLeft + (idx / Math.max(1, opts.chords.length)) * usableW;
        if (cx >= padLeft - 10 && cx <= width - padRight - 24) {
          ctx.fillText(String(ch.chord || ch), cx + 4, 16);
        }
      });
    }

    // Playhead cursor if provided
    if (opts.playheadSec != null && opts.playheadSec >= minT && opts.playheadSec <= maxT) {
      const px = padLeft + ((opts.playheadSec - minT) / span) * usableW;
      ctx.strokeStyle = "#C83E2B";
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.moveTo(px, trebleBaseY - 9 * stepPx);
      ctx.lineTo(px, bassBaseY + 2 * stepPx);
      ctx.stroke();
    }

    // Render note durations & heads
    for (const n of notes) {
      if (n.t + n.d < minT || n.t > maxT) continue;
      const x = padLeft + ((Math.max(minT, n.t) - minT) / span) * usableW;
      const endX = padLeft + ((Math.min(maxT, n.t + n.d) - minT) / span) * usableW;
      const tailW = Math.max(8, endX - x);

      const useTreble = n.h === "rh" || (n.h !== "lh" && n.p >= 60);
      const { diatonic, sharp } = pitchToDiatonicStep(n.p);
      // Treble E4 = diatonic step 4*7+2 = 30; Bass G2 = 2*7+4 = 18
      const refDiatonic = useTreble ? 30 : 18;
      const baseLineY = useTreble ? trebleBaseY : bassBaseY;
      const delta = clamp(diatonic - refDiatonic, -7, 13);
      const y = baseLineY - delta * stepPx;

      const isActive = opts.playheadSec != null && opts.playheadSec >= n.t && opts.playheadSec <= n.t + n.d;
      let fill = "#EAE6DF";
      if (n.h === "human") fill = "#A8C8E8";
      else if (n.h === "lh") fill = "#C4BDB0";
      if (isActive) fill = "#C83E2B";

      // Ledger lines if outside staff
      ctx.strokeStyle = "#4A463E";
      ctx.lineWidth = 1;
      if (delta < 0) {
        for (let ld = -2; ld >= delta; ld -= 2) {
          const ly = baseLineY - ld * stepPx;
          ctx.beginPath();
          ctx.moveTo(x - 7, ly);
          ctx.lineTo(x + 7, ly);
          ctx.stroke();
        }
      } else if (delta > 8) {
        for (let ld = 10; ld <= delta; ld += 2) {
          const ly = baseLineY - ld * stepPx;
          ctx.beginPath();
          ctx.moveTo(x - 7, ly);
          ctx.lineTo(x + 7, ly);
          ctx.stroke();
        }
      }

      // Sustain ribbon
      ctx.fillStyle = isActive ? "rgba(200, 62, 43, 0.38)" : fill === "#A8C8E8" ? "rgba(168, 200, 232, 0.25)" : "rgba(234, 230, 223, 0.16)";
      ctx.fillRect(x, y - stepPx * 0.45, tailW, stepPx * 0.9);

      // Note head
      ctx.fillStyle = fill;
      ctx.beginPath();
      ctx.ellipse(x + 4, y, stepPx * 0.95, stepPx * 0.68, -0.22, 0, Math.PI * 2);
      ctx.fill();

      if (sharp) {
        ctx.font = '600 13px "Cascadia Mono", monospace';
        ctx.fillText("#", x - 9, y + 4);
      }
    }

    if (notes.length === 0) {
      ctx.fillStyle = "#8B867C";
      ctx.font = '14px "Yu Gothic UI", sans-serif';
      ctx.fillText(opts.emptyText || "音符・フレーズの生成を待機しています...", padLeft + 16, trebleBaseY - 2 * stepPx);
    }
  }

  // ===========================================================================
  // Interactive Dual-Color Piano Keyboard Canvas (Mode 2: C3 48 .. C6 84)
  // ===========================================================================
  const PIANO_MIN = 48;
  const PIANO_MAX = 84;
  let pianoKeyHitboxes = [];

  function isBlackMidi(midi) {
    return [1, 3, 6, 8, 10].includes(((midi % 12) + 12) % 12);
  }

  function renderPianoKeyboard() {
    const canvas = $("canvas-piano-keys");
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    if (rect.width < 40 || rect.height < 40) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.floor(rect.width * dpr);
    const h = Math.floor(rect.height * dpr);
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }

    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const width = rect.width;
    const height = rect.height;

    ctx.fillStyle = state.projectionClean ? "#141517" : "#1C1D20";
    ctx.fillRect(0, 0, width, height);

    const now = performance.now();
    const humanSet = new Set(state.stage?.human_active_pitches || []);
    for (const [p, exp] of state.browserHumanKeys.entries()) {
      if (exp > now) humanSet.add(p);
      else state.browserHumanKeys.delete(p);
    }
    const outputSet = new Set(state.stage?.output?.active_pitches || []);

    const candList = state.stage?.candidates || [];
    const adoptedCand = candList.find((c) => c.adopted) || candList[0];
    const aiPreviewSet = new Set(
      (adoptedCand?.events || [])
        .map(normNote)
        .filter((n) => n && n.h !== "human")
        .slice(0, 8)
        .map((n) => n.p)
    );

    const whiteNotes = [];
    for (let m = PIANO_MIN; m <= PIANO_MAX; m++) {
      if (!isBlackMidi(m)) whiteNotes.push(m);
    }

    const whiteW = width / whiteNotes.length;
    const blackW = whiteW * 0.62;
    const blackH = height * 0.60;

    pianoKeyHitboxes = [];

    // Draw White Keys
    whiteNotes.forEach((m, idx) => {
      const x = idx * whiteW;
      let fill = "#EAE6DF";
      if (outputSet.has(m)) fill = "#C83E2B";
      else if (humanSet.has(m)) fill = "#A8C8E8";
      else if (aiPreviewSet.has(m)) fill = "#E5C88F";

      ctx.fillStyle = fill;
      ctx.fillRect(x + 1, 2, whiteW - 2, height - 4);
      ctx.strokeStyle = "#161513";
      ctx.lineWidth = 1;
      ctx.strokeRect(x + 1, 2, whiteW - 2, height - 4);

      if (m % 12 === 0 || m === PIANO_MIN) {
        ctx.fillStyle = fill === "#C83E2B" ? "#EAE6DF" : "#1D1B18";
        ctx.font = '600 13px "Cascadia Mono", monospace';
        ctx.fillText(midiToName(m), x + 5, height - 10);
      }

      pianoKeyHitboxes.push({ midi: m, black: false, x, y: 0, w: whiteW, h: height });
    });

    // Draw Black Keys on top
    for (let m = PIANO_MIN; m <= PIANO_MAX; m++) {
      if (!isBlackMidi(m)) continue;
      const whiteIdxBefore = whiteNotes.filter((wm) => wm < m).length;
      const x = whiteIdxBefore * whiteW - blackW * 0.5;
      let fill = "#141317";
      if (outputSet.has(m)) fill = "#C83E2B";
      else if (humanSet.has(m)) fill = "#A8C8E8";
      else if (aiPreviewSet.has(m)) fill = "#D4AF6A";

      ctx.fillStyle = fill;
      ctx.fillRect(x, 2, blackW, blackH);
      ctx.strokeStyle = "#3E3A32";
      ctx.lineWidth = 1;
      ctx.strokeRect(x, 2, blackW, blackH);

      pianoKeyHitboxes.unshift({ midi: m, black: true, x, y: 2, w: blackW, h: blackH });
    }
  }

  // ===========================================================================
  // Render Live State into Modes [1], [2], [3], and Diagnostic HUD
  // ===========================================================================
  function setPedalVisual(prefix, cc67, cc64) {
    const v67 = clamp(Math.round(cc67 || 0), 0, 127);
    const v64 = clamp(Math.round(cc64 || 0), 0, 127);
    const el67V = $(`${prefix}-p67-val`);
    const el67F = $(`${prefix}-p67-fill`);
    const el64V = $(`${prefix}-p64-val`);
    const el64F = $(`${prefix}-p64-fill`);
    if (el67V) el67V.textContent = String(v67);
    if (el67F) el67F.style.width = `${(v67 / 127) * 100}%`;
    if (el64V) el64V.textContent = String(v64);
    if (el64F) el64F.style.width = `${(v64 / 127) * 100}%`;
  }

  function updateActPills(containerId, actNum) {
    const box = $(containerId);
    if (!box) return;
    box.querySelectorAll(".act-pill").forEach((pill) => {
      const a = Number(pill.dataset.act || 1);
      pill.classList.toggle("active", a === actNum);
    });
  }

  function formatCandReason(cand) {
    if (!cand) return "比較待機中";
    if (cand.adopted) {
      const bs = cand.beauty_summary;
      if (bs && typeof bs === "object") {
        if (bs.summary_ja) return String(bs.summary_ja);
        const cons = Math.round(Number(bs.consonance_score ?? 0));
        const poly = Number(bs.avg_polyphony ?? 0).toFixed(1);
        const mono = Number(bs.mono_ratio_pct ?? 0).toFixed(1);
        return `採用: 協和 ${cons}% / 平均${poly}声 / 単音${mono}%`;
      }
      return String(bs || "採用（全監査項目パス・両手声部確定）");
    }
    return String(cand.rejection_tag_ja || cand.rejection_detail || "スコア基準未達により棄却");
  }

  function renderCandidateCards(modeKey, containerId, staffCanvasId, labelId) {
    const container = $(containerId);
    if (!container || !state.stage) return;
    const candidates = state.stage.candidates || [];
    const adopted = candidates.find((c) => c.adopted) || candidates[0];
    let selectedId = state.inspectedCand[modeKey];
    if (!selectedId || !candidates.some((c) => c.candidate_id === selectedId)) {
      selectedId = adopted ? adopted.candidate_id : "A";
      state.inspectedCand[modeKey] = selectedId;
    }

    // Rule 4: Update fixed candidate DOM slots in place (never destroy/recreate cards)
    const slots = Array.from(container.querySelectorAll(".cand-pick-card"));
    const slotIds = ["A", "B", "C", "D"];
    slotIds.forEach((slotCid, idx) => {
      const card = slots[idx];
      if (!card) return;
      const cand = candidates.find((c) => String(c.candidate_id) === slotCid) || candidates[idx];
      const cid = cand ? String(cand.candidate_id || slotCid) : slotCid;
      const isAdopted = Boolean(cand?.adopted);
      const isInspecting = cid === selectedId;
      const pct = cand && cand.total_score != null ? (cand.total_score <= 1.0 ? cand.total_score * 100 : cand.total_score).toFixed(1) : "--";
      const reasonText = formatCandReason(cand);

      card.classList.toggle("adopted", isAdopted);
      card.classList.toggle("rejected", !isAdopted);
      card.classList.toggle("inspecting", isInspecting);
      card.setAttribute("data-candidate-id", cid);

      const idEl = card.querySelector(".cand-pick-id");
      const scEl = card.querySelector(".cand-pick-score");
      const rngEl = card.querySelector(".cand-range");
      const rsnEl = card.querySelector(".cand-pick-reason");
      if (idEl) idEl.textContent = `案${cid}${isAdopted ? " · 採用" : " · 棄却"}`;
      if (scEl) scEl.textContent = `${pct}%`;
      if (rngEl) rngEl.textContent = cand ? `${cand.pitch_range_str || "--"} (${(cand.events || []).length}音)` : "--";
      if (rsnEl) rsnEl.textContent = reasonText;

      if (!card.dataset.slotBound) {
        card.dataset.slotBound = "1";
        card.addEventListener("click", () => {
          const clickedCid = card.getAttribute("data-candidate-id") || slotCid;
          state.inspectedCand[modeKey] = clickedCid;
          renderCandidateCards(modeKey, containerId, staffCanvasId, labelId);
        });
      }
    });

    const shown = candidates.find((c) => c.candidate_id === selectedId) || adopted;
    const lbl = $(labelId);
    if (lbl && shown) {
      lbl.textContent = shown.adopted
        ? `表示中: 案${shown.candidate_id}（採用候補）`
        : `表示中: 案${shown.candidate_id}（棄却案を比較確認中）`;
    }
    const chords = (state.stage.intent?.chord_progression || []).map((c) => ({ chord: c }));
    renderGrandStaff($(staffCanvasId), shown?.events || [], {
      chords,
      emptyText: "ターンを実行するとAIと人間の四声スコアがここに描画されます",
    });
  }

  function renderDiagnosticHud() {
    const s = state.stage || {};
    const th = s.telemetry_hud || {};
    const out = s.output || {};

    const connEl = $("diag-conn-status");
    if (connEl) {
      if (!LIVE_MODE) connEl.textContent = "公開WebAudioデモ";
      else connEl.textContent = state.wsConnected ? "WS 60fps 稼働中" : "HTTP 同期";
    }
    const parEl = $("diag-parallel");
    if (parEl && th.parallel_compose_ms != null) {
      parEl.textContent = `${Number(th.parallel_compose_ms).toFixed(1)} ms`;
    }
    const llmEl = $("diag-llm");
    if (llmEl && th.llm_ttft_ms != null) {
      llmEl.textContent = `${Math.round(th.llm_ttft_ms)} ms`;
    }
    const specEl = $("diag-speculative");
    if (specEl && th.speculative_hits != null) {
      const total = Number(th.speculative_hits || 0) + Number(th.speculative_misses || 0);
      specEl.textContent = `${th.speculative_hits} / ${total}`;
    }
    const jitEl = $("diag-jitter");
    if (jitEl && th.l5_jitter_ms != null) {
      jitEl.textContent = `${Number(th.l5_jitter_ms).toFixed(2)} ms`;
    }
    const wrkEl = $("diag-workers");
    if (wrkEl && th.active_workers != null) {
      wrkEl.textContent = `${th.active_workers} / ${th.total_workers || 6}`;
    }
    const vEl = $("diag-voices");
    if (vEl) {
      const voices = LIVE_MODE ? Number(out.active_voices ?? th.active_voices ?? 0) : audio.activeVoices.size;
      vEl.textContent = `${voices} / ${out.hard_limit || 30} 音`;
    }
    const vramEl = $("diag-vram");
    if (vramEl && th.vram_used_gb != null) {
      vramEl.textContent = `${Number(th.vram_used_gb).toFixed(1)} / ${Number(th.vram_total_gb || 8).toFixed(1)} GB`;
    }
  }

  function renderStageViews() {
    const s = state.stage;
    if (!s) return;

    const p = s.perception || {};
    const intent = s.intent || {};
    const out = s.output || {};
    const th = s.telemetry_hud || {};
    const chords = intent.chord_progression || [];
    const primaryChord = chords[0] || intent.tonal_center || "Dm";
    const actNum = Number(intent.act_number || 1);
    const turnId = Number(s.turn_id || 0);
    if (state.lastRenderedTurnId < 0) {
      const adoptedNew = (s.candidates || []).find((c) => c.adopted);
      if (adoptedNew) {
        if (!state.inspectedCand.cello) state.inspectedCand.cello = adoptedNew.candidate_id;
        if (!state.inspectedCand.piano) state.inspectedCand.piano = adoptedNew.candidate_id;
      }
    }
    state.lastRenderedTurnId = turnId;

    // Sync top connection button state in LIVE_MODE
    if (LIVE_MODE) {
      state.connProduction = Boolean(s.production_mode);
      const connBtn = $("btn-conn-mode");
      if (connBtn) {
        connBtn.classList.toggle("production", state.connProduction);
        connBtn.textContent = state.connProduction ? "接続: 本番実機 (Disklavier/UR22)" : "接続: デモ（仮想演奏）";
      }
      const demoBtn = $("btn-cello-demo");
      if (demoBtn) {
        demoBtn.classList.toggle("vermilion", Boolean(s.demo_enabled));
        demoBtn.textContent = s.demo_enabled ? "仮想チェロデモ停止" : "仮想チェロでデモ開始";
      }
      const monBtn = $("btn-audio-monitor");
      if (monBtn) {
        state.monitorAudio = Boolean(s.monitor_audio);
        monBtn.classList.toggle("active", state.monitorAudio);
        monBtn.setAttribute("aria-pressed", state.monitorAudio ? "true" : "false");
        monBtn.textContent = `PC音声: ${state.monitorAudio ? "ON" : "OFF"}`;
      }
      const listenBtn = $("btn-piano-live-listen");
      if (listenBtn) {
        const ll = Boolean(s.live_listening);
        listenBtn.classList.toggle("primary-fill", ll);
        listenBtn.classList.toggle("active", ll);
        listenBtn.setAttribute("aria-pressed", ll ? "true" : "false");
        listenBtn.textContent = `リアルタイム即興待受: ${ll ? "ON" : "OFF"}`;
      }
    }

    // Mode [1] Cello View
    if (state.activeView === "cello") {
      updateActPills("cello-act-arc", actNum);
      if ($("cello-chord-val")) $("cello-chord-val").textContent = primaryChord;
      if ($("cello-chord-roman")) $("cello-chord-roman").textContent = intent.phrase_function || "i";
      if ($("cello-key-tonal")) $("cello-key-tonal").textContent = `${intent.tonal_center || "Dm"} / Turn #${turnId}`;
      if ($("cello-monologue") && intent.monologue_ja) $("cello-monologue").textContent = intent.monologue_ja;
      if ($("cello-f0-val")) {
        const hz = p.f0_hz ? `${Number(p.f0_hz).toFixed(1)} Hz` : "-- Hz";
        $("cello-f0-val").textContent = `${p.note_name || "--"} (${hz})`;
      }
      if ($("cello-art-val")) {
        $("cello-art-val").textContent = `${p.articulation || "Cantabile"} / ${Math.round(p.vibrato_cents || 0)} ct`;
      }
      if ($("cello-erle-val")) {
        const erle = Number(p.nlms_erle_db || 0).toFixed(1);
        $("cello-erle-val").textContent = `+${erle} dB / ${p.echo_suppressed_count || 0}件`;
      }
      const breathPct = clamp(Math.round(Number(p.breath_gauge || 0) * 100), 0, 100);
      if ($("cello-breath-val")) $("cello-breath-val").textContent = `${breathPct}%${p.turn_ready ? " (応答可)" : ""}`;
      if ($("cello-breath-fill")) {
        $("cello-breath-fill").style.width = `${breathPct}%`;
        $("cello-breath-fill").classList.toggle("ready", Boolean(p.turn_ready));
      }
      setPedalVisual("cello", out.pedal_cc67 ?? p.pedal_cc67, out.pedal_cc64 ?? p.pedal_cc64);
      renderCandidateCards("cello", "cello-cand-cards", "canvas-cello-hero-staff", "cello-hero-cand-label");
    }

    // Mode [2] Piano View
    if (state.activeView === "piano") {
      updateActPills("piano-act-arc", actNum);
      if ($("piano-chord-val")) $("piano-chord-val").textContent = primaryChord;
      if ($("piano-chord-roman")) $("piano-chord-roman").textContent = intent.phrase_function || "i";
      if ($("piano-key-tonal")) $("piano-key-tonal").textContent = `${intent.tonal_center || "Dm"} / Turn #${turnId}`;
      if ($("piano-monologue") && intent.monologue_ja) $("piano-monologue").textContent = intent.monologue_ja;
      if ($("piano-echo-val")) $("piano-echo-val").textContent = `${p.echo_suppressed_count || 0} 音`;
      if ($("piano-voices-val")) $("piano-voices-val").textContent = `${out.active_voices || 0} / ${out.hard_limit || 30} 音`;
      if ($("piano-spec-val")) {
        $("piano-spec-val").textContent = th.speculative_ready ? "準備完了 (即応)" : "生成待機中";
      }
      const breathPct = clamp(Math.round(Number(p.breath_gauge || 0) * 100), 0, 100);
      if ($("piano-breath-val")) $("piano-breath-val").textContent = `${breathPct}%`;
      if ($("piano-breath-fill")) {
        $("piano-breath-fill").style.width = `${breathPct}%`;
        $("piano-breath-fill").classList.toggle("ready", Boolean(p.turn_ready));
      }
      setPedalVisual("piano", out.pedal_cc67 ?? p.pedal_cc67, out.pedal_cc64 ?? p.pedal_cc64);
      renderPianoKeyboard();
      renderCandidateCards("piano", "piano-cand-cards", "canvas-piano-hero-staff", "piano-hero-cand-label");
    }

    // Mode [3] NotaGen Live View
    if (state.activeView === "notagen") {
      const ng = s.notagen || state.notagen;
      renderNotaGenView(ng);
    }

    renderDiagnosticHud();
  }

  function renderNotaGenView(ng) {
    if (!ng) return;
    const statusMap = {
      idle: "待機中",
      streaming: "リアルタイム生成・即時発音中",
      generating: "リアルタイム生成中",
      completed: "生成完了",
      stopped: "停止済み",
      error: "エラー発生",
    };
    const statusText = statusMap[ng.status] || ng.status || "待機中";
    if ($("ng-status-pill")) {
      $("ng-status-pill").textContent = `${statusText} / エンジン: ${ng.active_backend || "auto"}`;
    }
    if ($("ng-metrics-sub")) {
      const ttft = ng.ttft_ms ? `${Math.round(ng.ttft_ms)} ms` : "-- ms";
      $("ng-metrics-sub").textContent = `TTFT: ${ttft} / ${ng.bars_completed || 0} / ${ng.target_bars || 8} 小節`;
    }
    const abcText = ng.abc_stream_text || ng.abc_text || ng.stream_text || "";
    if ($("ng-abc-console") && abcText) {
      const pre = $("ng-abc-console");
      pre.textContent = abcText;
      pre.scrollTop = pre.scrollHeight;
    }
    const aud = ng.audit || {};
    if ($("ng-guard-summary")) {
      if (Number(aud.beauty_score || 0) > 0) {
        $("ng-guard-summary").textContent = `音楽性監査 ${Number(aud.beauty_score).toFixed(1)}点 / 協和 ${Number(aud.consonance_score || 0).toFixed(0)}% / 平均${Number(aud.avg_polyphony || 0).toFixed(1)}声`;
      } else if (ng.guard_summary) {
        $("ng-guard-summary").textContent = ng.guard_summary;
      }
    }
    if ($("ng-commentary")) {
      if (ng.commentary_ja) {
        $("ng-commentary").textContent = ng.commentary_ja;
      } else if (ng.status === "completed") {
        $("ng-commentary").textContent = `${ng.bars_completed || 0}小節の一からの生成が完了（${ng.active_backend || "engine"} / 平均${Number(aud.avg_polyphony || 0).toFixed(1)}声部 / 監査 ${Number(aud.beauty_score || 0).toFixed(1)}点）。`;
      } else if (ng.last_error) {
        $("ng-commentary").textContent = `生成警告: ${ng.last_error}`;
      }
    }
    const lastCurve = (arr, fallback) =>
      Array.isArray(arr) && arr.length > 0 ? Number(arr[arr.length - 1].val ?? arr[arr.length - 1].value ?? fallback) : Number(fallback ?? 0);
    setPedalVisual("ng", lastCurve(ng.cc67_curve, ng.pedal_cc67), lastCurve(ng.cc64_curve, ng.pedal_cc64));

    const ngNotes = ng.events || ng.notes || [];
    if (state.activeView === "notagen") {
      renderGrandStaff($("canvas-notagen-hero"), ngNotes, {
        chords: ng.chord_timeline || [],
        emptyText: "生成を開始すると、和声ガード済みの両手楽譜が1小節ずつリアルタイムに描画されます",
      });
    }
  }

  // ===========================================================================
  // Interactive Piano Action & Offline AI Dialogue Simulator
  // ===========================================================================
  function triggerOfflineDialogueReply(triggerPitches, modeKey) {
    const pitches = triggerPitches.length ? triggerPitches : [62, 65, 69];
    const root = pitches[0];
    const isMinor = true;
    const third = isMinor ? root + 3 : root + 4;
    const fifth = root + 7;
    const bass = root - 24 >= 33 ? root - 24 : root - 12;

    const events = [
      ...pitches.map((p, i) => ({ pitch: p, onset_s: i * 0.35, duration_s: 0.65, velocity: 68, hand: "human" })),
      { pitch: bass, onset_s: 0.25, duration_s: 2.1, velocity: 54, hand: "lh" },
      { pitch: bass + 7, onset_s: 0.65, duration_s: 1.6, velocity: 48, hand: "lh" },
      { pitch: third + 12, onset_s: 0.85, duration_s: 1.4, velocity: 60, hand: "rh" },
      { pitch: fifth + 12, onset_s: 1.25, duration_s: 1.3, velocity: 62, hand: "rh" },
      { pitch: root + 14, onset_s: 1.65, duration_s: 1.5, velocity: 58, hand: "rh" },
    ];

    const rootName = NOTE_NAMES[((root % 12) + 12) % 12] + "m";
    state.stage = {
      ...state.stage,
      mode: modeKey,
      turn_id: (state.stage?.turn_id || 0) + 1,
      perception: {
        ...state.stage?.perception,
        f0_hz: midiToFreq(pitches[pitches.length - 1]),
        note_name: midiToName(pitches[pitches.length - 1]),
        pedal_cc64: 74,
        pedal_cc67: 44,
        breath_gauge: 0.88,
        turn_ready: true,
      },
      output: {
        active_pitches: [bass, third + 12, fifth + 12],
        active_voices: 4,
        hard_limit: 30,
        pedal_cc64: 74,
        pedal_cc67: 44,
      },
      intent: {
        act_number: ((state.stage?.turn_id || 0) % 4) + 1,
        tonal_center: rootName,
        chord_progression: [rootName, `${rootName}9`],
        monologue_ja: `人間の入力モチーフ (${pitches.map(midiToName).join("–")}) を検出し、${midiToName(bass)} バス支えと上声部 ${midiToName(third + 12)}–${midiToName(root + 14)} で応答。`,
      },
      candidates: [
        {
          candidate_id: "A",
          label_ja: "案A — 4声和声応答",
          total_score: 0.954,
          adopted: true,
          pitch_range_str: `${midiToName(bass)}–${midiToName(root + 14)}`,
          beauty_summary: `採用: 入力モチーフの共通音を保持しつつ両手ポリフォニーで応答 (95.4%)`,
          events,
        },
        ...(state.stage?.candidates || []).slice(1, 4),
      ],
    };
    state.inspectedCand[modeKey] = "A";
    renderStageViews();

    if (state.monitorAudio) {
      events
        .filter((e) => e.hand !== "human")
        .forEach((e) => audio.playNote(e.pitch, e.velocity, e.duration_s, { delayS: e.onset_s, cc64: 74, cc67: 44 }));
    }
  }

  let offlineReplyTimer = null;
  const recentHumanQueue = [];

  function handleHumanPianoKey(midi, velocity = 72) {
    state.browserHumanKeys.set(midi, performance.now() + 550);
    if (state.monitorAudio) {
      audio.playNote(midi, velocity, 0.7, { human: true, cc64: 64 });
    }
    if ($("piano-last-input")) {
      $("piano-last-input").textContent = `人間の打鍵: ${midiToName(midi)} (vel ${velocity})`;
    }
    renderPianoKeyboard();

    if (LIVE_MODE) {
      postJson("/api/stage/human_note", {
        pitch: midi,
        velocity,
        duration_s: 0.48,
        from_browser: true,
      });
    } else {
      recentHumanQueue.push(midi);
      if (recentHumanQueue.length > 5) recentHumanQueue.shift();
      clearTimeout(offlineReplyTimer);
      offlineReplyTimer = setTimeout(() => {
        triggerOfflineDialogueReply(recentHumanQueue.slice(-4), "piano");
      }, 420);
    }
  }

  function auditionCandidateEvents(modeKey) {
    const candidates = state.stage?.candidates || [];
    const cid = state.inspectedCand[modeKey];
    const cand = candidates.find((c) => c.candidate_id === cid) || candidates[0];
    if (!cand || !cand.events?.length) return;
    audio.stopAll();
    const t0 = Math.min(...cand.events.map((e) => Number(e.onset_s ?? e.t ?? 0)));
    cand.events.forEach((raw) => {
      const n = normNote(raw);
      if (!n) return;
      audio.playNote(n.p, n.v, n.d, {
        delayS: Math.max(0, n.t - t0),
        human: n.h === "human",
        cc64: 72,
        cc67: 36,
      });
    });
  }

  // ===========================================================================
  // Mode 3: Offline ABC Streaming Simulation (for static GitHub Pages showcase)
  // ===========================================================================
  function stopOfflineNotaGen() {
    if (state.notagen.simTimer) {
      clearInterval(state.notagen.simTimer);
      state.notagen.simTimer = null;
    }
    state.notagen.status = "stopped";
    renderNotaGenView(state.notagen);
  }

  function startOfflineNotaGen(keySig, targetBars, seedText) {
    stopOfflineNotaGen();
    const rootByKeys = { Dm: 62, Cm: 60, Am: 57, F: 65, C: 60, Eb: 63 };
    const root = rootByKeys[keySig] || 62;
    const isMin = keySig.endsWith("m");
    const scale = isMin ? [0, 2, 3, 5, 7, 8, 10, 12] : [0, 2, 4, 5, 7, 9, 11, 12];
    const barChords = [
      [0, 2, 4],
      [3, 5, 7],
      [4, 6, 8],
      [0, 2, 4],
    ];

    state.notagen = {
      ...state.notagen,
      status: "streaming",
      active_backend: "browser_abc_demo",
      bars_completed: 0,
      target_bars: targetBars,
      ttft_ms: 48,
      key_sig: keySig,
      abc_text: `X:1\nT:Live Demo (${seedText})\nM:4/4\nL:1/8\nK:${keySig}\n`,
      notes: [],
      guard_summary: "和声・声部連結ガード監視中",
      commentary_ja: "種モチーフを受け取り、1小節ずつABC記譜法と両手ポリフォニーを生成・検証しています。",
      pedal_cc64: 76,
      pedal_cc67: 44,
    };
    renderNotaGenView(state.notagen);

    let barIdx = 0;
    state.notagen.simTimer = setInterval(() => {
      barIdx += 1;
      const chordDeg = barChords[(barIdx - 1) % barChords.length];
      const baseT = (barIdx - 1) * 1.6;
      const rhLine = [];
      const lhLine = [];
      const newNotes = [];

      const bassMidi = root - 24 + scale[chordDeg[0] % 7];
      const fifthMidi = bassMidi + 7;
      newNotes.push({ p: bassMidi, t: baseT, d: 1.5, v: 54, h: "lh" });
      newNotes.push({ p: fifthMidi, t: baseT + 0.4, d: 1.1, v: 46, h: "lh" });
      lhLine.push(`${midiToName(bassMidi).replace(/\d/, "")},4`, `${midiToName(fifthMidi).replace(/\d/, "")},4`);

      for (let step = 0; step < 4; step++) {
        const deg = scale[(chordDeg[step % chordDeg.length] + (barIdx % 3)) % scale.length];
        const m = root + deg;
        const harm = m - (step === 0 ? 5 : 4);
        const dt = baseT + step * 0.4;
        newNotes.push({ p: m, t: dt, d: 0.48, v: 64 + (barIdx === targetBars - 1 ? 12 : 0), h: "rh" });
        if (step % 2 === 0) {
          newNotes.push({ p: harm, t: dt, d: 0.72, v: 50, h: "rh" });
        }
        rhLine.push(NOTE_NAMES[m % 12] + "2");
      }

      const cc64 = barIdx === targetBars ? 92 : 76;
      const cc67 = barIdx >= targetBars - 1 ? 88 : 34;

      state.notagen.bars_completed = barIdx;
      state.notagen.pedal_cc64 = cc64;
      state.notagen.pedal_cc67 = cc67;
      state.notagen.notes = [...state.notagen.notes, ...newNotes];
      state.notagen.abc_text += `[V:1] ${rhLine.join(" ")} |\n[V:2] ${lhLine.join(" ")} |\n`;
      state.notagen.guard_summary = `第${barIdx}/${targetBars}小節 検証通過 (不協和対: 0% / 声部連結OK)`;

      if ($("chk-ng-autoplay")?.checked && state.monitorAudio) {
        newNotes.forEach((n) => audio.playNote(n.p, n.v, n.d, { delayS: Math.max(0, n.t - baseT), cc64, cc67 }));
      }

      if (barIdx >= targetBars) {
        clearInterval(state.notagen.simTimer);
        state.notagen.simTimer = null;
        state.notagen.status = "completed";
        state.notagen.commentary_ja = `${targetBars}小節の一からの生成が完了しました。全小節でバス対位線・右手和声補完・左ペダル終止処理が検証済みです。`;
      }
      renderNotaGenView(state.notagen);
    }, 850);
  }

  // ===========================================================================
  // Mode 4: Curated 100-Piece Library, Stem Mixer, X-Ray & Time-Machine
  // ===========================================================================
  function classifyLayer(note) {
    if (note.h === "human" || (note.r || "").includes("cello") || (note.r || "").includes("human")) {
      return "human";
    }
    if (note.h === "lh") return "bass";
    if ((note.r || "").includes("melody")) return "melody";
    return "harmony";
  }

  function isLayerAudible(layerKey) {
    const anySolo = Object.values(state.layers).some((l) => l.solo);
    const layer = state.layers[layerKey];
    if (!layer) return true;
    if (anySolo) return layer.solo && !layer.mute;
    return !layer.mute;
  }

  function renderMixerControls() {
    const list = $("mixer-list");
    if (!list) return;
    const meta = [
      { key: "human", name: "人間・主役動機", desc: "Human / Cello Motif (#A8C8E8)" },
      { key: "melody", name: "AI 右手主旋律", desc: "Cantabile RH Soprano (#EAE6DF)" },
      { key: "harmony", name: "AI 右手内声和音", desc: "Harmonic Voicing" },
      { key: "bass", name: "AI 左手バス・対位", desc: "LH Bass / Arpeggio" },
    ];
    list.innerHTML = "";
    meta.forEach((m) => {
      const st = state.layers[m.key];
      const row = document.createElement("div");
      row.className = "mixer-row";
      row.innerHTML = `
        <div class="mixer-label">
          <span class="mixer-name">${m.name}</span>
          <span class="mixer-desc">${m.desc}</span>
        </div>
        <div class="mixer-btns">
          <button class="btn compact ${st.solo ? "active" : ""}" type="button" data-layer="${m.key}" data-action="solo">SOLO</button>
          <button class="btn compact ${st.mute ? "vermilion active" : ""}" type="button" data-layer="${m.key}" data-action="mute">MUTE</button>
        </div>
      `;
      row.querySelectorAll("button").forEach((btn) => {
        btn.addEventListener("click", () => {
          const act = btn.dataset.action;
          state.layers[m.key][act] = !state.layers[m.key][act];
          renderMixerControls();
        });
      });
      list.appendChild(row);
    });
  }

  function renderLibraryPieceList() {
    const listEl = $("piece-list");
    if (!listEl || !state.catalog) return;
    const q = (state.searchText || "").trim().toLowerCase();
    const pieces = (state.catalog.pieces || []).filter((p) => {
      if (state.activeFilter === "mp3" && !p.showcase_mp3) return false;
      if (state.activeFilter === "cello" && p.mode !== "cello") return false;
      if (state.activeFilter === "piano" && p.mode !== "piano") return false;
      if (state.activeStyle !== "all" && p.lh_texture !== state.activeStyle) return false;
      if (q) {
        const hay = `${p.id} ${p.title_ja} ${p.title_en} ${p.tonality_ja} ${p.tonality}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    state.filteredPieces = pieces;

    if ($("catalog-stats")) {
      $("catalog-stats").textContent = `${pieces.length} / ${(state.catalog.pieces || []).length}曲`;
    }

    listEl.innerHTML = "";
    pieces.forEach((p) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `piece-item ${state.selectedPieceMeta?.id === p.id ? "selected" : ""}`;
      btn.setAttribute("data-piece-id", p.id);
      btn.innerHTML = `
        <div class="piece-item-top">
          <span class="piece-id-tag">#${String(p.index).padStart(3, "0")} · ${p.mode === "cello" ? "チェロ" : "ピアノ"} · ${p.tonality_ja || p.tonality}</span>
          ${p.showcase_mp3 ? '<span class="mp3-tag">MP3</span>' : ""}
        </div>
        <div class="piece-title">${p.title_ja || p.title_en}</div>
        <div class="piece-meta">
          <span>監査 ${Number(p.beauty_score || 0).toFixed(1)}点 / ${Number(p.avg_polyphony || 0).toFixed(1)}声</span>
          <span>${Math.round(p.duration_s || 0)}s</span>
        </div>
      `;
      btn.addEventListener("click", () => selectLibraryPiece(p));
      listEl.appendChild(btn);
    });
  }

  function renderLibraryDeliberation(piece) {
    const delib = piece?.deliberation || {};
    if ($("delib-memo")) {
      $("delib-memo").textContent = delib.monologue_ja || piece?.composer_note_ja || "監査メモはありません。";
    }
    const grid = $("delib-grid");
    if (!grid) return;

    const list = [];
    if (delib.adopted) list.push({ ...delib.adopted, isAdopted: true });
    (delib.rejected || []).forEach((r) => list.push({ ...r, isAdopted: false }));

    const slots = Array.from(grid.querySelectorAll(".cand-card"));
    if (slots.length >= 4) {
      slots.forEach((card, idx) => {
        const cand = list[idx];
        const isAdopted = Boolean(cand?.isAdopted);
        card.className = `cand-card ${isAdopted ? "adopted" : "rejected"}`;
        const titleEl = card.querySelector(".cand-title");
        const tagEl = card.querySelector(".cand-tag");
        const mc = card.querySelector("canvas");
        if (cand) {
          if (titleEl) titleEl.textContent = cand.label_ja || `案${cand.id || idx + 1}`;
          if (tagEl) {
            tagEl.className = `cand-tag ${isAdopted ? "ok" : ""}`;
            tagEl.textContent = `${Number(cand.score || 0).toFixed(1)}点 · ${isAdopted ? "採用" : cand.tag_ja || "棄却"}`;
          }
          if (mc) requestAnimationFrame(() => renderGrandStaff(mc, cand.notes || []));
        } else {
          if (titleEl) titleEl.textContent = `案${["A", "B", "C", "D"][idx]}`;
          if (tagEl) tagEl.textContent = "比較データなし";
        }
      });
      return;
    }

    grid.innerHTML = "";
    list.forEach((cand) => {
      const card = document.createElement("div");
      card.className = `cand-card ${cand.isAdopted ? "adopted" : "rejected"}`;
      card.innerHTML = `
        <div class="cand-head">
          <span class="cand-title">${cand.label_ja || `案${cand.id}`}</span>
          <span class="cand-tag ${cand.isAdopted ? "ok" : ""}">${Number(cand.score || 0).toFixed(1)}点 · ${cand.isAdopted ? "採用" : cand.tag_ja || "棄却"}</span>
        </div>
        <canvas class="mini-canvas"></canvas>
      `;
      grid.appendChild(card);
      const mc = card.querySelector("canvas");
      requestAnimationFrame(() => renderGrandStaff(mc, cand.notes || []));
    });
  }

  function renderLibraryXray(piece, sec) {
    if (!piece) return;
    const tl = piece.chord_timeline || [];
    const curIdx = Math.max(
      0,
      tl.findIndex((c) => sec >= c.start_s && sec <= c.end_s)
    );
    const cur = tl[curIdx] || tl[0];
    const nxt = tl[curIdx + 1];

    if ($("xray-chord")) $("xray-chord").textContent = cur ? cur.chord : "--";
    if ($("xray-roman")) $("xray-roman").textContent = cur ? cur.roman : "";
    if ($("xray-next")) $("xray-next").textContent = nxt ? `次: ${nxt.chord}` : "終止";

    const getValAt = (arr, t) => {
      let val = 0;
      for (const item of arr || []) {
        if (item.t <= t) val = item.val;
        else break;
      }
      return val;
    };
    const p67 = getValAt(piece.cc67, sec);
    const p64 = getValAt(piece.cc64, sec);
    if ($("xray-pedal")) $("xray-pedal").textContent = `左L(67)=${p67} · 右R(64)=${p64}`;
    if ($("xray-pedals")) $("xray-pedals").textContent = `${p67} / ${p64}`;

    const activeNow = (piece.notes || []).filter((n) => sec >= n.t && sec <= n.t + n.d).length;
    if ($("xray-poly")) $("xray-poly").textContent = `${activeNow} 声`;

    const aud = piece.audit || {};
    if ($("xray-audit")) $("xray-audit").textContent = `${Number(aud.beauty_score || 0).toFixed(1)}%`;

    const strip = $("chord-strip");
    if (strip && tl.length) {
      if (strip.children.length !== Math.min(16, tl.length)) {
        strip.innerHTML = "";
        tl.slice(0, 16).forEach((ch, idx) => {
          const chip = document.createElement("button");
          chip.type = "button";
          chip.className = "chord-chip";
          chip.textContent = ch.chord;
          chip.dataset.idx = String(idx);
          chip.addEventListener("click", () => seekLibraryTo(ch.start_s));
          strip.appendChild(chip);
        });
      }
      Array.from(strip.children).forEach((el, idx) => {
        el.classList.toggle("now", idx === curIdx);
      });
    }

    const barsEl = $("audit-bars");
    if (barsEl && aud.beauty_score != null && !barsEl.dataset.renderedForId) {
      barsEl.dataset.renderedForId = piece.id;
      const items = [
        ["総合美観", aud.beauty_score],
        ["協和度", aud.consonance_score],
        ["声部充実", aud.polyphony_score],
        ["旋律歌謡", aud.melody_cantabile_score],
        ["ペダル清澄", aud.pedal_cleanliness_score],
      ];
      barsEl.innerHTML = items
        .map(
          ([lbl, val]) => `
          <div class="audit-bar-item">
            <div>${lbl}: ${Number(val || 0).toFixed(0)}%</div>
            <div class="audit-bar-bg"><div class="audit-bar-fill" style="width:${clamp(Number(val || 0), 0, 100)}%"></div></div>
          </div>`
        )
        .join("");
    }
  }

  function renderLibraryFrame(sec) {
    const piece = state.pieceData;
    if (!piece) return;
    const dur = Math.max(1, Number(piece.duration_s || 45));
    const clamped = clamp(sec, 0, dur);
    if ($("time-readout")) $("time-readout").textContent = `${formatTime(clamped)} / ${formatTime(dur)}`;
    if ($("seek-slider")) $("seek-slider").value = String(Math.round((clamped / dur) * 1000));

    // VU decay
    audio.lastLevelDb = Math.max(-58, audio.lastLevelDb - 1.2);
    const vuPct = clamp(((audio.lastLevelDb + 58) / 58) * 100, 0, 100);
    if ($("vu-fill")) $("vu-fill").style.width = `${vuPct}%`;
    if ($("vu-db")) $("vu-db").textContent = audio.lastLevelDb <= -55 ? "-inf dB" : `${audio.lastLevelDb.toFixed(0)} dB`;

    const winStart = Math.max(0, clamped - 3.0);
    const winEnd = Math.min(dur, winStart + 9.5);
    renderGrandStaff($("score-canvas"), piece.notes || [], {
      winStart,
      winEnd,
      playheadSec: clamped,
      chords: piece.chord_timeline || [],
    });
    renderLibraryXray(piece, clamped);
  }

  function stopLibraryPlayback() {
    state.libPlaying = false;
    if (state.libRafId) {
      cancelAnimationFrame(state.libRafId);
      state.libRafId = null;
    }
    audio.stopAll();
    if ($("btn-play")) $("btn-play").textContent = "再生";
    const mp3 = $("mp3-player");
    if (mp3 && !mp3.paused) {
      mp3.pause();
    }
  }

  function seekLibraryTo(sec) {
    const piece = state.pieceData;
    if (!piece) return;
    const dur = Number(piece.duration_s || 45);
    state.libCursorSec = clamp(sec, 0, dur);
    const notes = piece.notes || [];
    state.libTriggeredIdx = notes.findIndex((n) => n.t >= state.libCursorSec);
    if (state.libTriggeredIdx < 0) state.libTriggeredIdx = notes.length;
    if (state.libPlaying) {
      audio.stopAll();
      state.libStartPerfMs = performance.now() - state.libCursorSec * 1000;
    }
    renderLibraryFrame(state.libCursorSec);
  }

  function stepLibraryLoop() {
    if (!state.libPlaying || !state.pieceData) return;
    const elapsed = (performance.now() - state.libStartPerfMs) / 1000;
    const dur = Number(state.pieceData.duration_s || 45);
    const notes = state.pieceData.notes || [];

    while (state.libTriggeredIdx < notes.length) {
      const raw = notes[state.libTriggeredIdx];
      if (raw.t > elapsed + 0.04) break;
      const n = normNote(raw);
      if (n && isLayerAudible(classifyLayer(n)) && state.monitorAudio) {
        audio.playNote(n.p, n.v, n.d, {
          human: n.h === "human",
          cc64: 72,
          cc67: 36,
        });
      }
      state.libTriggeredIdx += 1;
    }

    state.libCursorSec = elapsed;
    renderLibraryFrame(elapsed);

    if (elapsed >= dur) {
      stopLibraryPlayback();
      seekLibraryTo(0);
      return;
    }
    state.libRafId = requestAnimationFrame(stepLibraryLoop);
  }

  function startLibraryPlayback() {
    if (!state.pieceData) return;
    audio.ensure();
    state.libPlaying = true;
    if ($("btn-play")) $("btn-play").textContent = "一時停止";
    const notes = state.pieceData.notes || [];
    state.libTriggeredIdx = notes.findIndex((n) => n.t >= state.libCursorSec);
    if (state.libTriggeredIdx < 0) state.libTriggeredIdx = 0;
    state.libStartPerfMs = performance.now() - state.libCursorSec * 1000;
    state.libRafId = requestAnimationFrame(stepLibraryLoop);
  }

  async function selectLibraryPiece(meta) {
    stopLibraryPlayback();
    state.selectedPieceMeta = meta;
    renderLibraryPieceList();

    const jsonUrl = LIVE_MODE
      ? `/api/pieces/${meta.id}`
      : `./data/pieces/${meta.id}.json`;
    const piece = await fetchJson(jsonUrl);
    if (!piece) return;

    state.pieceData = piece;
    state.libCursorSec = 0;
    if ($("audit-bars")) delete $("audit-bars").dataset.renderedForId;

    if ($("work-title")) $("work-title").textContent = piece.title_ja || piece.title_en || meta.id;
    if ($("work-subtitle")) {
      $("work-subtitle").textContent = `#${meta.id} · ${piece.mode_label_ja || piece.mode} · BPM ${piece.bpm || 72} · ${piece.tonality_ja || piece.tonality}`;
    }
    if ($("composer-note")) $("composer-note").textContent = piece.composer_note_ja || "";

    const mp3Name = String(piece.showcase_mp3 || meta.showcase_mp3 || "").replace(/^.*[/\\]/, "");
    const mp3El = $("mp3-player");
    const mp3Stat = $("mp3-status");
    if (mp3El && mp3Name) {
      mp3El.preload = "auto";
      mp3El.src = LIVE_MODE ? `/audio/${mp3Name}` : `./audio/${mp3Name}`;
      mp3El.load();
      if (mp3Stat) mp3Stat.textContent = `スタジオ高音質MP3 (${mp3Name})`;
    } else if (mp3El) {
      mp3El.removeAttribute("src");
      mp3El.load();
      if (mp3Stat) mp3Stat.textContent = "リアルタイムWebAudioシンセ演奏対応";
    }

    renderLibraryDeliberation(piece);
    renderLibraryFrame(0);
  }

  async function initLibraryCatalog() {
    renderMixerControls();
    let cat = await fetchJson(LIVE_MODE ? "/api/catalog" : "./data/catalog_100.json");
    if (!cat && LIVE_MODE) {
      cat = await fetchJson("./data/catalog_100.json");
    }
    if (!cat || !Array.isArray(cat.pieces)) return;
    state.catalog = cat;

    const styleSel = $("style-select");
    if (styleSel) {
      const styles = Array.from(new Set(cat.pieces.map((p) => p.lh_texture).filter(Boolean)));
      styleSel.innerHTML = '<option value="all">全様式</option>';
      styles.forEach((st) => {
        const opt = document.createElement("option");
        opt.value = st;
        opt.textContent = STYLE_LABELS[st] || st;
        styleSel.appendChild(opt);
      });
    }

    renderLibraryPieceList();
    if (cat.pieces.length > 0) {
      const firstMp3 = cat.pieces.find((p) => p.showcase_mp3) || cat.pieces[0];
      await selectLibraryPiece(firstMp3);
    }
  }

  // ===========================================================================
  // Top-Level 4-Mode View Switching
  // ===========================================================================
  function syncMobileAccordions() {
    const isMobile = window.innerWidth <= 768;
    if (state._lastMobileLayout === isMobile) return;
    state._lastMobileLayout = isMobile;
    document.querySelectorAll(".mobile-accordion").forEach((det) => {
      if (isMobile) det.removeAttribute("open");
      else det.setAttribute("open", "");
    });
    if ($("diag-hud-bar") && $("btn-diag-toggle")) {
      state.diagVisible = !isMobile;
      $("diag-hud-bar").hidden = isMobile;
      $("btn-diag-toggle").classList.toggle("active", !isMobile);
      $("btn-diag-toggle").setAttribute("aria-pressed", isMobile ? "false" : "true");
    }
  }

  function switchModeView(targetView) {
    state.activeView = targetView;
    const views = ["cello", "piano", "notagen", "library"];
    views.forEach((v) => {
      const tab = $(`mode-tab-${v}`);
      const panel = $(`view-${v}`);
      const active = v === targetView;
      if (tab) tab.setAttribute("aria-selected", active ? "true" : "false");
      if (panel) panel.hidden = !active;
    });
    document.querySelectorAll(".mobile-nav-btn[data-mode-view]").forEach((btn) => {
      btn.setAttribute("aria-selected", btn.dataset.modeView === targetView ? "true" : "false");
    });

    if (LIVE_MODE && (targetView === "cello" || targetView === "piano")) {
      postJson("/api/stage/mode", { mode: targetView });
    }

    requestAnimationFrame(() => {
      renderStageViews();
      if (targetView === "library") renderLibraryFrame(state.libCursorSec);
      if (targetView === "piano") renderPianoKeyboard();
    });
  }

  function toggleProjectionClean(forceState) {
    state.projectionClean = forceState != null ? Boolean(forceState) : !state.projectionClean;
    const shell = $("studio-shell");
    if (shell) shell.classList.toggle("projection-clean", state.projectionClean);
    requestAnimationFrame(() => {
      renderStageViews();
      if (state.activeView === "library") renderLibraryFrame(state.libCursorSec);
    });
  }

  // ===========================================================================
  // Live WebSocket (60fps Stage Sync)
  // ===========================================================================
  function connectLiveSocket() {
    if (!LIVE_MODE) return;
    let closedByUnload = false;
    window.addEventListener("beforeunload", () => {
      closedByUnload = true;
      if (state.ws) state.ws.close();
    });

    const openWs = () => {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${location.host}/ws/stage`);
      state.ws = ws;
      ws.onopen = () => {
        state.wsConnected = true;
        renderDiagnosticHud();
      };
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          state.stage = data;
          renderStageViews();
        } catch (_) {}
      };
      ws.onclose = () => {
        state.wsConnected = false;
        renderDiagnosticHud();
        if (!closedByUnload) {
          setTimeout(openWs, 1500);
        }
      };
      ws.onerror = () => {
        ws.close();
      };
    };
    openWs();

    fetchJson("/api/stage/state").then((data) => {
      if (data) {
        state.stage = data;
        renderStageViews();
      }
    });
  }

  // ===========================================================================
  // Wire All DOM Event Handlers
  // ===========================================================================
  function bindEvents() {
    // 4-Mode Tabs (Desktop + Mobile Bottom Navigation)
    document.querySelectorAll(".mode-tab[data-mode-view], .mobile-nav-btn[data-mode-view]").forEach((btn) => {
      btn.addEventListener("click", () => switchModeView(btn.dataset.modeView));
    });

    // Global Topbar Actions
    $("btn-onboarding-close")?.addEventListener("click", () => {
      if ($("onboarding-banner")) $("onboarding-banner").hidden = true;
    });

    $("btn-diag-toggle")?.addEventListener("click", () => {
      state.diagVisible = !state.diagVisible;
      if ($("diag-hud-bar")) $("diag-hud-bar").hidden = !state.diagVisible;
      $("btn-diag-toggle").classList.toggle("active", state.diagVisible);
      $("btn-diag-toggle").setAttribute("aria-pressed", state.diagVisible ? "true" : "false");
    });

    $("btn-help")?.addEventListener("click", () => {
      if ($("help-modal")) $("help-modal").hidden = false;
    });
    $("btn-help-close")?.addEventListener("click", () => {
      if ($("help-modal")) $("help-modal").hidden = true;
    });
    $("help-modal")?.addEventListener("click", (e) => {
      if (e.target === $("help-modal")) $("help-modal").hidden = true;
    });

    $("btn-proj-f10")?.addEventListener("click", () => toggleProjectionClean(true));
    $("btn-proj-exit")?.addEventListener("click", () => toggleProjectionClean(false));

    $("btn-audio-monitor")?.addEventListener("click", () => {
      state.monitorAudio = !state.monitorAudio;
      audio.setMuted(!state.monitorAudio);
      $("btn-audio-monitor").classList.toggle("active", state.monitorAudio);
      $("btn-audio-monitor").setAttribute("aria-pressed", state.monitorAudio ? "true" : "false");
      $("btn-audio-monitor").textContent = `PC音声: ${state.monitorAudio ? "ON" : "OFF"}`;
      if (LIVE_MODE) postJson("/api/stage/audio_monitor", { enabled: state.monitorAudio });
    });

    $("btn-conn-mode")?.addEventListener("click", () => {
      if (!LIVE_MODE) {
        alert("GitHub Pages 公開デモ版では WebAudio と収録MP3で再生されます。ローカルPCの stage_server 起動時に実機接続へ切り替えできます。");
        return;
      }
      const nextProd = !state.connProduction;
      postJson("/api/stage/connection_mode", { production_mode: nextProd });
    });

    const handlePanic = () => {
      audio.stopAll();
      stopLibraryPlayback();
      stopOfflineNotaGen();
      if (LIVE_MODE) {
        postJson("/api/stage/panic", {});
        postJson("/api/notagen/stop", {});
      }
    };
    $("btn-panic")?.addEventListener("click", handlePanic);

    // Mode [1] Cello Controls
    $("btn-cello-demo")?.addEventListener("click", () => {
      if (LIVE_MODE) {
        const nextDemo = !Boolean(state.stage?.demo_enabled);
        postJson("/api/stage/demo", { enabled: nextDemo });
      } else {
        triggerOfflineDialogueReply([50, 53, 57, 55], "cello");
      }
    });
    $("btn-cello-turn")?.addEventListener("click", () => {
      if (LIVE_MODE) postJson("/api/stage/turn", {});
      else triggerOfflineDialogueReply([57, 60, 62, 65], "cello");
    });
    $("btn-cello-retake")?.addEventListener("click", () => {
      if (LIVE_MODE) postJson("/api/stage/retake", { text: "別解釈でより豊かな響きを探索" });
      else triggerOfflineDialogueReply([53, 57, 60, 64], "cello");
    });
    $("btn-cello-cal")?.addEventListener("click", () => {
      if (LIVE_MODE) postJson("/api/stage/calibrate", {});
      else if ($("cello-erle-val")) $("cello-erle-val").textContent = "+16.8 dB / 較正完了";
    });
    $("btn-cello-reset")?.addEventListener("click", () => {
      if (LIVE_MODE) postJson("/api/stage/reset", {});
      else {
        state.stage = createDefaultOfflineStage("cello");
        renderStageViews();
      }
    });
    $("btn-cello-save")?.addEventListener("click", async () => {
      const res = await postJson("/api/stage/synthesize_work", { play_audio: false });
      showExportToast("cello-export-toast", res);
    });
    $("btn-cello-directive")?.addEventListener("click", () => {
      const text = $("inp-cello-directive")?.value?.trim();
      if (!text) return;
      if (LIVE_MODE) postJson("/api/stage/directive", { text });
      else triggerOfflineDialogueReply([50, 55, 57], "cello");
    });
    $("sel-cello-preset")?.addEventListener("change", (e) => {
      if (LIVE_MODE) postJson("/api/stage/preset", { preset: e.target.value });
    });
    $("btn-cello-audition")?.addEventListener("click", () => auditionCandidateEvents("cello"));

    document.querySelectorAll("[data-cello-phrase]").forEach((btn) => {
      const map = {
        question: [
          { pitch: 50, duration_s: 0.6, velocity: 64 },
          { pitch: 53, duration_s: 0.6, velocity: 68 },
          { pitch: 57, duration_s: 0.8, velocity: 72 },
        ],
        cantabile: [
          { pitch: 57, duration_s: 0.55, velocity: 72 },
          { pitch: 61, duration_s: 0.55, velocity: 76 },
          { pitch: 64, duration_s: 0.55, velocity: 80 },
          { pitch: 67, duration_s: 0.85, velocity: 74 },
        ],
        lament: [
          { pitch: 58, duration_s: 0.6, velocity: 84 },
          { pitch: 57, duration_s: 0.6, velocity: 78 },
          { pitch: 55, duration_s: 0.6, velocity: 70 },
          { pitch: 53, duration_s: 1.0, velocity: 62 },
        ],
      };
      btn.addEventListener("click", () => {
        const phrase = map[btn.dataset.celloPhrase] || map.question;
        if (state.monitorAudio) {
          phrase.forEach((n, idx) => audio.playNote(n.pitch, n.velocity, n.duration_s, { delayS: idx * 0.35, human: true }));
        }
        if (LIVE_MODE) {
          postJson("/api/stage/human_phrase", { notes: phrase, mode: "cello", auto_turn: true });
        } else {
          triggerOfflineDialogueReply(phrase.map((n) => n.pitch), "cello");
        }
      });
    });

    // Mode [2] Piano Controls & Canvas Mouse Clicks
    $("btn-piano-live-listen")?.addEventListener("click", () => {
      const cur = state.stage?.live_listening !== false;
      if (LIVE_MODE) postJson("/api/stage/live_listen", { enabled: !cur });
    });
    $("btn-piano-demo-phrase")?.addEventListener("click", () => {
      const phrase = [
        { pitch: 62, duration_s: 0.45, velocity: 68 },
        { pitch: 65, duration_s: 0.45, velocity: 72 },
        { pitch: 69, duration_s: 0.45, velocity: 76 },
        { pitch: 64, duration_s: 0.85, velocity: 70 },
      ];
      phrase.forEach((n, idx) => audio.playNote(n.pitch, n.velocity, n.duration_s, { delayS: idx * 0.32, human: true }));
      if (LIVE_MODE) {
        postJson("/api/stage/human_phrase", { notes: phrase, mode: "piano", auto_turn: true });
      } else {
        triggerOfflineDialogueReply(phrase.map((n) => n.pitch), "piano");
      }
    });
    $("btn-piano-pedal-cue")?.addEventListener("click", () => {
      if (LIVE_MODE) postJson("/api/stage/pedal_cue", { control: 67, value: 96 });
      else triggerOfflineDialogueReply([62, 69, 72], "piano");
    });
    $("btn-piano-turn")?.addEventListener("click", () => {
      if (LIVE_MODE) postJson("/api/stage/turn", {});
      else triggerOfflineDialogueReply([62, 65, 69], "piano");
    });
    $("btn-piano-retake")?.addEventListener("click", () => {
      if (LIVE_MODE) postJson("/api/stage/retake", { text: "ピアノ同士の対話をより精緻に再構成" });
      else triggerOfflineDialogueReply([60, 64, 67, 71], "piano");
    });
    $("btn-piano-save")?.addEventListener("click", async () => {
      const res = await postJson("/api/stage/synthesize_work", { play_audio: false });
      showExportToast("piano-export-toast", res);
    });
    $("btn-piano-directive")?.addEventListener("click", () => {
      const text = $("inp-piano-directive")?.value?.trim();
      if (!text) return;
      if (LIVE_MODE) postJson("/api/stage/directive", { text });
      else triggerOfflineDialogueReply([62, 69], "piano");
    });
    $("sel-piano-preset")?.addEventListener("change", (e) => {
      if (LIVE_MODE) postJson("/api/stage/preset", { preset: e.target.value });
    });
    $("btn-piano-audition")?.addEventListener("click", () => auditionCandidateEvents("piano"));

    $("canvas-piano-keys")?.addEventListener("pointerdown", (e) => {
      const canvas = $("canvas-piano-keys");
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const hit = pianoKeyHitboxes.find((k) => x >= k.x && x <= k.x + k.w && y >= k.y && y <= k.y + k.h);
      if (hit) handleHumanPianoKey(hit.midi, 76);
    });

    // Mode [3] NotaGen / LLM Live Streaming Controls
    $("btn-ng-start")?.addEventListener("click", async () => {
      const prompt_ja = $("inp-ng-prompt")?.value || "静かな問いかけから始まり、静かに閉じる作品";
      const engine = $("sel-ng-backend")?.value || "auto";
      const key_sig = $("sel-ng-key")?.value || "Dm";
      const tonalityMap = {
        Dm: "D_minor",
        Cm: "C_minor",
        Am: "A_minor",
        F: "F_major",
        C: "C_major",
        Eb: "Eb_major",
      };
      const tonality = tonalityMap[key_sig] || "D_minor";
      const seedRaw = ($("inp-ng-seed")?.value || "D4 F4 A4").trim();
      const bars = Number($("sel-ng-bars")?.value || 8);
      const play_audio = Boolean($("chk-ng-autoplay")?.checked);

      if (LIVE_MODE) {
        const res = await postJson("/api/notagen/start", {
          prompt_ja,
          engine,
          tonality,
          seed_notes: seedRaw,
          bars,
          play_audio,
          blocking: false,
        });
        const ngState = res?.notagen || res;
        if (ngState) {
          state.notagen = ngState;
          if (state.stage) state.stage.notagen = ngState;
          renderNotaGenView(ngState);
        }
      } else {
        startOfflineNotaGen(key_sig, bars, seedRaw);
      }
    });
    $("btn-ng-stop")?.addEventListener("click", () => {
      audio.stopAll();
      if (LIVE_MODE) postJson("/api/notagen/stop", {});
      else stopOfflineNotaGen();
    });
    $("btn-ng-play-audio")?.addEventListener("click", () => {
      const ng = state.stage?.notagen || state.notagen;
      const notes = (ng?.notes || ng?.events || []).map(normNote).filter(Boolean);
      if (!notes.length) return;
      audio.stopAll();
      const t0 = Math.min(...notes.map((n) => n.t));
      notes.forEach((n) => audio.playNote(n.p, n.v, n.d, { delayS: Math.max(0, n.t - t0), cc64: 74, cc67: 38 }));
    });
    $("btn-ng-save")?.addEventListener("click", async () => {
      const res = await postJson("/api/notagen/save_work", {});
      showExportToast("ng-export-toast", res);
    });

    // Mode [4] Library Archive Controls
    $("search-input")?.addEventListener("input", (e) => {
      state.searchText = e.target.value;
      renderLibraryPieceList();
    });
    $("style-select")?.addEventListener("change", (e) => {
      state.activeStyle = e.target.value;
      renderLibraryPieceList();
    });
    document.querySelectorAll(".filter-btn[data-filter]").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".filter-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        state.activeFilter = btn.dataset.filter;
        renderLibraryPieceList();
      });
    });

    $("btn-play")?.addEventListener("click", () => {
      if (state.libPlaying) stopLibraryPlayback();
      else startLibraryPlayback();
    });
    $("btn-stop")?.addEventListener("click", () => {
      stopLibraryPlayback();
      seekLibraryTo(0);
    });
    $("seek-slider")?.addEventListener("input", (e) => {
      const dur = Number(state.pieceData?.duration_s || 45);
      seekLibraryTo((Number(e.target.value) / 1000) * dur);
    });

    $("score-canvas")?.addEventListener("click", (e) => {
      if (!state.pieceData) return;
      const rect = e.target.getBoundingClientRect();
      const dur = Number(state.pieceData.duration_s || 45);
      const winStart = Math.max(0, state.libCursorSec - 3.0);
      const winEnd = Math.min(dur, winStart + 9.5);
      const ratio = clamp((e.clientX - rect.left - 58) / Math.max(80, rect.width - 80), 0, 1);
      seekLibraryTo(winStart + ratio * (winEnd - winStart));
    });

    $("tab-score")?.addEventListener("click", () => {
      $("tab-score").setAttribute("aria-selected", "true");
      $("tab-delib").setAttribute("aria-selected", "false");
      $("panel-score").hidden = false;
      $("panel-delib").hidden = true;
      requestAnimationFrame(() => renderLibraryFrame(state.libCursorSec));
    });
    $("tab-delib")?.addEventListener("click", () => {
      $("tab-score").setAttribute("aria-selected", "false");
      $("tab-delib").setAttribute("aria-selected", "true");
      $("panel-score").hidden = true;
      $("panel-delib").hidden = false;
      renderLibraryDeliberation(state.pieceData);
    });

    $("btn-resume-ai")?.addEventListener("click", () => {
      const chord = $("xray-chord")?.textContent || "Dm";
      if ($("inp-ng-seed")) $("inp-ng-seed").value = "D4 F4 A4 C5";
      if ($("inp-ng-prompt")) {
        $("inp-ng-prompt").value = `${state.selectedPieceMeta?.title_ja || "選定曲"} (${chord} 付近) の余韻を継承し、新たな展開を続ける`;
      }
      switchModeView("notagen");
    });

    // Global Keyboard Shortcuts (F10 Projection, Esc Panic/Close, A-K Piano Keys)
    window.addEventListener("keydown", (e) => {
      if (e.key === "F10") {
        e.preventDefault();
        toggleProjectionClean();
        return;
      }
      if (e.key === "Escape") {
        if ($("help-modal") && !$("help-modal").hidden) {
          $("help-modal").hidden = true;
        } else {
          handlePanic();
        }
        return;
      }
      const tag = (document.activeElement?.tagName || "").toUpperCase();
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

      if (e.code === "Space" && state.activeView === "library") {
        e.preventDefault();
        if (state.libPlaying) stopLibraryPlayback();
        else startLibraryPlayback();
        return;
      }
      if (state.activeView === "piano" && !e.repeat && PC_KEY_TO_MIDI[e.code] != null) {
        handleHumanPianoKey(PC_KEY_TO_MIDI[e.code], 78);
      }
    });

    window.addEventListener("resize", () => {
      syncMobileAccordions();
      renderStageViews();
      if (state.activeView === "library") renderLibraryFrame(state.libCursorSec);
      if (state.activeView === "piano") renderPianoKeyboard();
    });
  }

  // ===========================================================================
  // Bootstrap
  // ===========================================================================
  document.addEventListener("DOMContentLoaded", () => {
    syncMobileAccordions();
    bindEvents();
    initLibraryCatalog();
    renderStageViews();
    if (LIVE_MODE) connectLiveSocket();
  });
})();
