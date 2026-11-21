/* Disklavier-Auto Studio & GitHub Pages Interactive Player (High-Craft Dark Editorial) */
(function () {
  "use strict";

  const COLOR_STAGE = "#060608";
  const COLOR_ADOPTED = "#EAE6DF";
  const COLOR_SECONDARY = "#8F8A80";
  const COLOR_REJECTED = "#5A5852";
  const COLOR_VERMILION = "#C83E2B";

  const LIVE_MODE = !!window.DISKLAVIER_LIVE_MODE;

  const state = {
    catalog: null,
    filtered: [],
    currentPiece: null,
    filterMode: "all", // all | showcase | cello | piano
    filterTonality: "all",
    filterMinScore: 0,
    filterSearch: "",
    mixer: {
      rh: true,
      lh: true,
      human: true,
      cc64: true,
      cc67: true,
      solo: null, // null | "rh" | "lh" | "human"
    },
    playback: {
      playing: false,
      startCtxTime: 0,
      offsetS: 0,
      durationS: 36,
      scheduledIdx: 0,
      sortedNotes: [],
      timerId: null,
      rafId: null,
    },
    delibTimers: [],
    audioCtx: null,
    masterComp: null,
    reverbSend: null,
    analyser: null,
    gains: { rh: null, lh: null, human: null },
    panners: { rh: null, lh: null, human: null },
    activeVoiceNodes: new Set(),
  };

  const TEXTURE_JA = {
    open_arpeggio_10th: "開離10度アルペジオ",
    broken_chord_barcarolle: "分散和音 (バルカローレ)",
    chorale_voicing: "4声コラール和音",
    countermelody_tenor: "内声テノール対旋律",
    alberti_lyrical: "抒情アルベルティ伴奏",
  };

  function formatTextureJa(tex) {
    return TEXTURE_JA[tex] || tex || "開離和音伴奏";
  }

  function el(id) {
    return document.getElementById(id);
  }

  function fmtTime(sec) {
    const s = Math.max(0, Math.floor(sec || 0));
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
  }

  function midiToFreq(m) {
    return 440.0 * Math.pow(2.0, (m - 69) / 12.0);
  }

  function pieceUrl(pieceId) {
    return LIVE_MODE ? `/api/pieces/${pieceId}` : `./data/pieces/${pieceId}.json`;
  }

  function catalogUrl() {
    return LIVE_MODE ? "/api/catalog" : "./data/catalog_100.json";
  }

  function audioAssetUrl(relPath) {
    if (!relPath) return "";
    const fname = relPath.split("/").pop();
    return LIVE_MODE ? `/audio/${fname}` : `./audio/${fname}`;
  }

  /* ---- Pure JS Client-Side SMF Type-1 MIDI Generator ---- */
  function writeVarLen(val) {
    let v = Math.max(0, val | 0);
    const bytes = [v & 0x7f];
    while ((v >>= 7) > 0) {
      bytes.unshift((v & 0x7f) | 0x80);
    }
    return bytes;
  }

  function strBytes(str) {
    const out = [];
    for (let i = 0; i < str.length; i++) {
      const code = str.charCodeAt(i);
      out.push(code < 128 ? code : 63);
    }
    return out;
  }

  function u32Bytes(n) {
    return [(n >>> 24) & 0xff, (n >>> 16) & 0xff, (n >>> 8) & 0xff, n & 0xff];
  }

  function u16Bytes(n) {
    return [(n >>> 8) & 0xff, n & 0xff];
  }

  function encodeTrack(events) {
    events.sort((a, b) => a.tick - b.tick || a.order - b.order);
    const body = [];
    let prevTick = 0;
    for (const ev of events) {
      const dt = Math.max(0, ev.tick - prevTick);
      prevTick = ev.tick;
      body.push(...writeVarLen(dt), ...ev.bytes);
    }
    body.push(0x00, 0xff, 0x2f, 0x00); // End of track
    return [...strBytes("MTrk"), ...u32Bytes(body.length), ...body];
  }

  function buildMidiUint8(piece) {
    const tpq = 480;
    const bpm = Math.max(40, Math.min(180, Number(piece.bpm || 76)));
    const usPerBeat = Math.round(60000000 / bpm);
    const ticksPerSec = (tpq * bpm) / 60.0;

    const tempoTrack = encodeTrack([
      { tick: 0, order: 0, bytes: [0xff, 0x51, 0x03, (usPerBeat >> 16) & 0xff, (usPerBeat >> 8) & 0xff, usPerBeat & 0xff] },
      { tick: 0, order: 1, bytes: [0xff, 0x58, 0x04, 0x04, 0x02, 0x18, 0x08] },
    ]);

    const humanEvs = [
      { tick: 0, order: 0, bytes: [0xc0, piece.mode === "cello" ? 42 : 0] },
    ];
    const rhEvs = [{ tick: 0, order: 0, bytes: [0xc1, 0] }];
    const lhEvs = [{ tick: 0, order: 0, bytes: [0xc2, 0] }];

    for (const n of piece.notes || []) {
      const t0 = Math.max(0, Math.round(Number(n.t) * ticksPerSec));
      const t1 = Math.max(t0 + 24, Math.round((Number(n.t) + Number(n.d)) * ticksPerSec));
      const p = Math.max(21, Math.min(108, Number(n.p) | 0));
      const v = Math.max(18, Math.min(112, Number(n.v || 68) | 0));
      const target = n.h === "human" ? humanEvs : (n.h === "lh" ? lhEvs : rhEvs);
      const ch = n.h === "human" ? 0 : (n.h === "lh" ? 2 : 1);
      target.push({ tick: t0, order: 2, bytes: [0x90 | ch, p, v] });
      target.push({ tick: t1, order: 1, bytes: [0x80 | ch, p, 24] });
    }

    for (const pt of piece.cc64 || []) {
      const tick = Math.max(0, Math.round(Number(pt.t) * ticksPerSec));
      const val = Math.max(0, Math.min(127, Number(pt.val) | 0));
      lhEvs.push({ tick, order: 1, bytes: [0xb2, 64, val] });
    }
    for (const pt of piece.cc67 || []) {
      const tick = Math.max(0, Math.round(Number(pt.t) * ticksPerSec));
      const val = Math.max(0, Math.min(127, Number(pt.val) | 0));
      lhEvs.push({ tick, order: 1, bytes: [0xb2, 67, val] });
    }

    const tr1 = encodeTrack(humanEvs);
    const tr2 = encodeTrack(rhEvs);
    const tr3 = encodeTrack(lhEvs);
    const header = [
      ...strBytes("MThd"),
      ...u32Bytes(6),
      ...u16Bytes(1), // SMF Type 1
      ...u16Bytes(4), // 4 tracks
      ...u16Bytes(tpq),
    ];
    return new Uint8Array([...header, ...tempoTrack, ...tr1, ...tr2, ...tr3]);
  }

  function downloadCurrentMidi() {
    if (!state.currentPiece) return;
    const bytes = buildMidiUint8(state.currentPiece);
    const blob = new Blob([bytes], { type: "audio/midi" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${state.currentPiece.id}.mid`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  /* ---- WebAudio Polyphonic Synth + Stereo Hall Reverb + 5-Channel Mixer ---- */
  function buildChamberHallIR(ctx, rt60Sec) {
    const sr = ctx.sampleRate || 44100;
    const len = Math.floor(sr * rt60Sec);
    const ir = ctx.createBuffer(2, len, sr);
    let seed = 1841;
    const nextRand = () => {
      seed = (seed * 1664525 + 1013904223) >>> 0;
      return (seed / 4294967296) * 2 - 1;
    };
    const earlyL = [[0.012, 0.24], [0.021, -0.17], [0.032, 0.14], [0.047, 0.10]];
    const earlyR = [[0.015, 0.22], [0.025, 0.16], [0.037, -0.13], [0.052, 0.09]];
    for (let ch = 0; ch < 2; ch++) {
      const data = ir.getChannelData(ch);
      const taps = ch === 0 ? earlyL : earlyR;
      for (const [dt, g] of taps) {
        const idx = Math.min(len - 1, Math.floor(dt * sr));
        data[idx] += g;
      }
      let lp = 0;
      for (let i = 0; i < len; i++) {
        const t = i / sr;
        const fadeIn = Math.min(1, Math.max(0, (t - 0.016) / 0.045));
        const dec = Math.exp((-6.9078 * t) / rt60Sec) * fadeIn;
        lp = lp * 0.72 + nextRand() * 0.28;
        data[i] += lp * dec * 0.22;
      }
    }
    return ir;
  }

  function ensureAudioGraph() {
    if (!state.audioCtx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return false;
      state.audioCtx = new AC();
      const comp = state.audioCtx.createDynamicsCompressor();
      comp.threshold.value = -14;
      comp.knee.value = 18;
      comp.ratio.value = 3.5;
      comp.attack.value = 0.005;
      comp.release.value = 0.24;

      const dryBus = state.audioCtx.createGain();
      dryBus.gain.value = 0.88;
      dryBus.connect(comp);

      const reverbSend = state.audioCtx.createGain();
      reverbSend.gain.value = 0.26;
      const convolver = state.audioCtx.createConvolver();
      convolver.buffer = buildChamberHallIR(state.audioCtx, 1.25);
      reverbSend.connect(convolver);
      convolver.connect(comp);

      const analyser = state.audioCtx.createAnalyser();
      analyser.fftSize = 256;
      comp.connect(analyser);
      analyser.connect(state.audioCtx.destination);

      const panMap = { rh: 0.16, lh: -0.22, human: -0.18 };
      for (const ch of ["rh", "lh", "human"]) {
        const g = state.audioCtx.createGain();
        g.gain.value = 1.0;
        if (typeof state.audioCtx.createStereoPanner === "function") {
          const pan = state.audioCtx.createStereoPanner();
          pan.pan.value = panMap[ch];
          g.connect(pan);
          pan.connect(dryBus);
          pan.connect(reverbSend);
          state.panners[ch] = pan;
        } else {
          g.connect(dryBus);
          g.connect(reverbSend);
        }
        state.gains[ch] = g;
      }
      state.masterComp = comp;
      state.reverbSend = reverbSend;
      state.analyser = analyser;
      applyMixerGains();
    }
    if (state.audioCtx.state === "suspended") {
      state.audioCtx.resume().catch(() => {});
    }
    return true;
  }

  function isPartAudible(part) {
    if (state.mixer.solo) {
      return state.mixer.solo === part;
    }
    return !!state.mixer[part];
  }

  function applyMixerGains() {
    if (!state.audioCtx) return;
    const now = state.audioCtx.currentTime;
    for (const part of ["rh", "lh", "human"]) {
      const g = state.gains[part];
      if (g) {
        g.gain.setTargetAtTime(isPartAudible(part) ? 1.0 : 0.0, now, 0.015);
      }
    }
  }

  function valueAtCurve(curve, tSec) {
    if (!curve || !curve.length) return 0;
    let cur = Number(curve[0].val || 0);
    for (let i = 0; i < curve.length; i++) {
      if (Number(curve[i].t) <= tSec + 0.005) {
        cur = Number(curve[i].val || 0);
      } else {
        break;
      }
    }
    return cur;
  }

  function scheduleNoteWebAudio(note, pieceTimeS, ctxStartTime) {
    if (!state.audioCtx || state.audioCtx.state !== "running") return;
    const part = note.h === "human" ? "human" : (note.h === "lh" ? "lh" : "rh");
    const targetGain = state.gains[part];
    if (!targetGain) return;

    const cc64 = state.mixer.cc64 ? valueAtCurve(state.currentPiece?.cc64, pieceTimeS) : 0;
    const cc67 = state.mixer.cc67 ? valueAtCurve(state.currentPiece?.cc67, pieceTimeS) : 0;
    const veil = Math.max(0, Math.min(1, cc67 / 127.0));
    const damperExt = (cc64 / 127.0) * 0.72;

    const isCello = part === "human" && state.currentPiece?.mode === "cello";
    const durBase = Math.max(0.10, Number(note.d || 0.35));
    const dur = isCello ? durBase + 0.08 : Math.min(3.2, durBase + damperExt);
    const f0 = midiToFreq(Number(note.p));
    const velNorm = Math.max(0.18, Math.min(1.0, Number(note.v || 68) / 110.0));
    const peakAmp = (isCello ? 0.14 : 0.13) * velNorm * (1.0 - 0.16 * veil);

    const t0 = Math.max(state.audioCtx.currentTime + 0.004, ctxStartTime);
    const filter = state.audioCtx.createBiquadFilter();
    const env = state.audioCtx.createGain();

    const osc1 = state.audioCtx.createOscillator();
    const osc2 = state.audioCtx.createOscillator();
    const osc3 = state.audioCtx.createOscillator();
    const osc2Gain = state.audioCtx.createGain();
    const osc3Gain = state.audioCtx.createGain();

    if (isCello) {
      osc1.type = "sawtooth";
      osc1.frequency.setValueAtTime(f0, t0);
      osc2.type = "triangle";
      osc2.frequency.setValueAtTime(f0 * 2.0, t0);
      osc2Gain.gain.value = 0.24;
      osc3.type = "sine";
      osc3.frequency.setValueAtTime(f0 * 3.0, t0);
      osc3Gain.gain.value = 0.07;

      filter.type = "lowpass";
      filter.Q.value = 1.9;
      filter.frequency.setValueAtTime(1650 * (0.7 + 0.4 * velNorm), t0);
      filter.frequency.exponentialRampToValueAtTime(980, t0 + Math.min(dur, 0.35));

      env.gain.setValueAtTime(0.0001, t0);
      env.gain.linearRampToValueAtTime(peakAmp, t0 + 0.055);
      env.gain.setValueAtTime(peakAmp * 0.88, t0 + Math.max(0.08, dur - 0.08));
      env.gain.exponentialRampToValueAtTime(0.0006, t0 + dur + 0.14);
    } else {
      osc1.type = "triangle";
      osc1.frequency.setValueAtTime(f0, t0);
      osc2.type = "sine";
      osc2.frequency.setValueAtTime(f0 * 2.0, t0);
      osc2Gain.gain.value = 0.30 * (1.0 - 0.45 * veil);
      osc3.type = "sine";
      osc3.frequency.setValueAtTime(f0 * 3.0, t0);
      osc3Gain.gain.value = 0.09 * velNorm * (1.0 - 0.60 * veil);

      filter.type = "lowpass";
      filter.Q.value = 0.7;
      const brightHz = Math.max(900, (5400 * (0.65 + 0.45 * velNorm)) - veil * 3500);
      filter.frequency.setValueAtTime(brightHz, t0);
      filter.frequency.exponentialRampToValueAtTime(Math.max(480, brightHz * 0.42), t0 + Math.min(dur, 0.45));

      const atk = 0.005 + veil * 0.013;
      env.gain.setValueAtTime(0.0001, t0);
      env.gain.linearRampToValueAtTime(peakAmp, t0 + atk);
      env.gain.exponentialRampToValueAtTime(peakAmp * 0.44, t0 + Math.min(dur, atk + 0.19));
      env.gain.exponentialRampToValueAtTime(0.0006, t0 + dur + 0.20);
    }

    osc1.connect(filter);
    osc2.connect(osc2Gain);
    osc2Gain.connect(filter);
    osc3.connect(osc3Gain);
    osc3Gain.connect(filter);
    filter.connect(env);
    env.connect(targetGain);

    const stopAt = t0 + dur + 0.25;
    const voiceRec = { osc1, osc2, osc3, env };
    state.activeVoiceNodes.add(voiceRec);
    osc1.onended = () => {
      state.activeVoiceNodes.delete(voiceRec);
      try {
        osc1.disconnect();
        osc2.disconnect();
        osc3.disconnect();
        filter.disconnect();
        env.disconnect();
      } catch (_) {}
    };

    osc1.start(t0);
    osc2.start(t0);
    osc3.start(t0);
    osc1.stop(stopAt);
    osc2.stop(stopAt);
    osc3.stop(stopAt);
  }

  function silenceAllWebAudio() {
    if (state.audioCtx) {
      const now = state.audioCtx.currentTime;
      for (const rec of state.activeVoiceNodes) {
        try {
          rec.env.gain.cancelScheduledValues(now);
          rec.env.gain.setTargetAtTime(0.0001, now, 0.015);
          rec.osc1.stop(now + 0.06);
          rec.osc2.stop(now + 0.06);
          if (rec.osc3) rec.osc3.stop(now + 0.06);
        } catch (_) {}
      }
    }
    state.activeVoiceNodes.clear();
  }

  function currentPlaybackTimeS() {
    if (!state.playback.playing || !state.audioCtx) {
      return state.playback.offsetS;
    }
    const elapsed = state.audioCtx.currentTime - state.playback.startCtxTime;
    return Math.min(state.playback.durationS, Math.max(0, state.playback.offsetS + elapsed));
  }

  function stopPlayback(resetZero) {
    if (state.playback.timerId) {
      clearInterval(state.playback.timerId);
      state.playback.timerId = null;
    }
    if (state.playback.rafId) {
      cancelAnimationFrame(state.playback.rafId);
      state.playback.rafId = null;
    }
    if (state.playback.playing) {
      state.playback.offsetS = resetZero ? 0 : currentPlaybackTimeS();
    } else if (resetZero) {
      state.playback.offsetS = 0;
    }
    state.playback.playing = false;
    silenceAllWebAudio();
    const mp3El = el("mp3-player");
    if (mp3El && !mp3El.paused && resetZero) {
      mp3El.pause();
      mp3El.currentTime = 0;
    }
    el("btn-play").textContent = "再生 (WebAudio)";
    el("btn-play").classList.remove("vermilion");
    updateRealtimeTelemetry(state.playback.offsetS);
    drawScoreCanvas(state.playback.offsetS);
  }

  function startPlayback(fromSec) {
    if (!state.currentPiece) return;
    if (!ensureAudioGraph()) return;
    stopPlayback(false);

    const mp3El = el("mp3-player");
    if (mp3El && !mp3El.paused) mp3El.pause();

    const startAt = fromSec !== undefined ? fromSec : state.playback.offsetS;
    const dur = Math.max(1, Number(state.currentPiece.duration_s || 36));
    const offset = startAt >= dur - 0.15 ? 0 : Math.max(0, startAt);

    const notes = [...(state.currentPiece.notes || [])].sort((a, b) => a.t - b.t);
    let idx = 0;
    while (idx < notes.length && Number(notes[idx].t) < offset - 0.02) idx++;

    state.playback.playing = true;
    state.playback.offsetS = offset;
    state.playback.durationS = dur;
    state.playback.startCtxTime = state.audioCtx.currentTime;
    state.playback.sortedNotes = notes;
    state.playback.scheduledIdx = idx;

    el("btn-play").textContent = "一時停止";
    el("btn-play").classList.add("vermilion");

    const scheduleTick = () => {
      if (!state.playback.playing) return;
      const playPos = currentPlaybackTimeS();
      const lookahead = playPos + 0.18;
      while (state.playback.scheduledIdx < state.playback.sortedNotes.length) {
        const n = state.playback.sortedNotes[state.playback.scheduledIdx];
        if (Number(n.t) > lookahead) break;
        const delta = Number(n.t) - playPos;
        if (delta >= -0.04) {
          scheduleNoteWebAudio(n, Number(n.t), state.audioCtx.currentTime + Math.max(0, delta));
        }
        state.playback.scheduledIdx++;
      }
      if (playPos >= state.playback.durationS) {
        stopPlayback(true);
      }
    };

    scheduleTick();
    state.playback.timerId = setInterval(scheduleTick, 40);

    const rafLoop = () => {
      if (!state.playback.playing) return;
      const tNow = currentPlaybackTimeS();
      updateRealtimeTelemetry(tNow);
      drawScoreCanvas(tNow);
      state.playback.rafId = requestAnimationFrame(rafLoop);
    };
    state.playback.rafId = requestAnimationFrame(rafLoop);
  }

  /* ---- Realtime Telemetry, Harmony X-Ray & VU ---- */
  function updateRealtimeTelemetry(tSec) {
    const piece = state.currentPiece;
    if (!piece) return;

    const dur = Math.max(1, Number(piece.duration_s || 36));
    el("time-readout").textContent = `${fmtTime(tSec)} / ${fmtTime(dur)}`;
    const slider = el("seek-slider");
    if (slider) {
      slider.value = String(Math.round((tSec / dur) * 1000));
    }

    // Active Act highlight
    const acts = piece.acts || [];
    let curAct = 1;
    for (const a of acts) {
      if (Number(a.start_s) <= tSec + 0.05) curAct = Number(a.act);
    }
    for (let a = 1; a <= 4; a++) {
      const pill = el(`act-pill-${a}`);
      if (pill) pill.classList.toggle("active", a === curAct);
    }
    const activeActObj = acts.find((a) => Number(a.act) === curAct);
    if (activeActObj) {
      el("lh-texture-desc").textContent = `テクスチャ: ${formatTextureJa(activeActObj.lh_texture)}`;
    }

    // Current + Next chord in Harmony X-Ray
    const tl = piece.chord_timeline || [];
    let activeIdx = 0;
    for (let i = 0; i < tl.length; i++) {
      if (Number(tl[i].start_s) <= tSec + 0.05) activeIdx = i;
    }
    const ch = tl[activeIdx];
    const nextCh = tl[activeIdx + 1];
    if (ch) {
      el("xray-chord").textContent = ch.chord;
      el("xray-roman").textContent = `(${ch.roman})`;
      el("xray-function").textContent = `${ch.function_ja} / 第${ch.act}幕 ${ch.bar}小節`;
      const nextEl = el("xray-next-chord");
      if (nextEl) {
        nextEl.textContent = nextCh ? `→ ${nextCh.chord} (${nextCh.roman})` : "(終止着地)";
      }
    }
    const progWrap = el("xray-progression");
    if (progWrap && progWrap.children.length === tl.length) {
      for (let i = 0; i < tl.length; i++) {
        progWrap.children[i].classList.toggle("now", i === activeIdx);
      }
    }

    // Dual Pedal Live Gauges
    const cc64 = state.mixer.cc64 ? valueAtCurve(piece.cc64, tSec) : 0;
    const cc67 = state.mixer.cc67 ? valueAtCurve(piece.cc67, tSec) : 0;
    el("val-cc64").textContent = String(cc64);
    el("val-cc67").textContent = String(cc67);
    el("fill-cc64").style.width = `${Math.round((cc64 / 127) * 100)}%`;
    el("fill-cc67").style.width = `${Math.round((cc67 / 127) * 100)}%`;

    // Live simultaneous polyphony at tSec
    let sounding = 0;
    for (const n of piece.notes || []) {
      const nt = Number(n.t);
      if (nt <= tSec && tSec < nt + Number(n.d)) {
        const part = n.h === "human" ? "human" : (n.h === "lh" ? "lh" : "rh");
        if (isPartAudible(part)) sounding++;
      }
    }
    const audit = piece.audit || {};
    el("xray-polyphony").textContent = state.playback.playing
      ? `${sounding} 音 (平均 ${audit.avg_polyphony})`
      : `${audit.avg_polyphony} 音`;

    // Realtime dBFS VU Meter
    let dbfs = -60.0;
    if (state.playback.playing && state.analyser) {
      const buf = new Float32Array(state.analyser.fftSize);
      state.analyser.getFloatTimeDomainData(buf);
      let sumSq = 0;
      for (let i = 0; i < buf.length; i++) sumSq += buf[i] * buf[i];
      const rms = Math.sqrt(sumSq / buf.length);
      if (rms > 1e-5) dbfs = Math.max(-60, Math.min(0, 20 * Math.log10(rms)));
    }
    el("dbfs-readout").textContent = `${dbfs.toFixed(1)} dBFS`;
    const vuPct = Math.max(0, Math.min(100, ((dbfs + 60) / 54) * 100));
    el("vu-fill").style.width = `${vuPct}%`;
  }

  /* ---- 60fps Synchronized Grand Staff + Piano Roll + Dual-Pedal Canvas ---- */
  function midiToStaffStep(midi) {
    const semiToStep = [0, 0, 1, 1, 2, 3, 3, 4, 4, 5, 5, 6];
    const oct = Math.floor(midi / 12) - 1;
    return (oct - 4) * 7 + semiToStep[((midi % 12) + 12) % 12];
  }

  function drawScoreCanvas(playheadS) {
    const canvas = el("canvas-studio-score");
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(300, Math.floor(rect.width));
    const h = Math.max(220, Math.floor(rect.height));
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    if (canvas.width !== Math.floor(w * dpr) || canvas.height !== Math.floor(h * dpr)) {
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    ctx.fillStyle = COLOR_STAGE;
    ctx.fillRect(0, 0, w, h);

    const piece = state.currentPiece;
    if (!piece) return;

    const padL = 42;
    const padR = 14;
    const drawW = w - padL - padR;
    const dur = Math.max(1, Number(piece.duration_s || 36));

    // Layout: Top 46% Grand Staff, Middle 38% Piano Roll, Bottom 16% Pedal Strip
    const staffTop = 8;
    const staffH = Math.floor(h * 0.45);
    const rollTop = staffTop + staffH + 8;
    const rollH = Math.floor(h * 0.36);
    const pedTop = rollTop + rollH + 6;
    const pedBot = h - 6;

    // Barlines, Act markers, & chord labels
    const actNames = { 1: "I 提示", 2: "II 転調", 3: "III 頂点", 4: "IV 回想" };
    let lastChordLabelX = -999;
    ctx.font = "10px 'Cascadia Mono', monospace";
    for (const seg of piece.chord_timeline || []) {
      const x = padL + (Number(seg.start_s) / dur) * drawW;
      const barNum = Number(seg.bar);
      const isActStart = barNum % 4 === 1;
      ctx.strokeStyle = isActStart ? "#3A362E" : "#1B1916";
      ctx.lineWidth = isActStart ? 1.25 : 0.8;
      ctx.beginPath();
      ctx.moveTo(x, staffTop);
      ctx.lineTo(x, pedBot);
      ctx.stroke();

      if (isActStart) {
        const actNum = Number(seg.act || Math.ceil(barNum / 4));
        ctx.fillStyle = actNum === 3 ? "#8D3A2E" : "#6D685F";
        ctx.font = "9px 'Cascadia Mono', monospace";
        ctx.fillText(actNames[actNum] || `ACT ${actNum}`, x + 3, staffTop + 9);
      }

      if (x - lastChordLabelX >= 42) {
        ctx.font = "10px 'Cascadia Mono', monospace";
        ctx.fillStyle = Number(seg.start_s) <= playheadS && playheadS < Number(seg.end_s) ? COLOR_VERMILION : COLOR_SECONDARY;
        ctx.fillText(seg.chord, x + 3, staffTop + 21);
        lastChordLabelX = x;
      }
    }

    // Minimal top-right inline legend (spaced cleanly on Act IV header row)
    ctx.font = "9px 'Cascadia Mono', monospace";
    const legendText = piece.mode === "cello"
      ? "■RH  ■Vc  ■LH  ─CC#64  --CC#67"
      : "■RH  ■PfI  ■LH  ─CC#64  --CC#67";
    ctx.fillStyle = "#8C877E";
    const legW = ctx.measureText(legendText).width;
    ctx.fillText(legendText, Math.max(padL, w - padR - legW - 4), staffTop + 9);

    // Grand Staff lines + Left Brace Bar
    const stepPx = Math.max(2.5, (staffH - 12) / 26);
    const trebleBaseY = staffTop + 27 + stepPx * 9;
    const bassBaseY = trebleBaseY + stepPx * 11;
    ctx.strokeStyle = "#2A2823";
    ctx.lineWidth = 1;
    for (const baseY of [trebleBaseY, bassBaseY]) {
      for (let line = 0; line < 5; line++) {
        const y = Math.round(baseY - line * stepPx * 2) + 0.5;
        ctx.beginPath();
        ctx.moveTo(padL - 8, y);
        ctx.lineTo(w - padR, y);
        ctx.stroke();
      }
    }
    // Grand staff vertical bracket connecting treble & bass systems
    ctx.strokeStyle = "#48443C";
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.moveTo(padL - 8, trebleBaseY - 8 * stepPx);
    ctx.lineTo(padL - 8, bassBaseY);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(padL - 12, trebleBaseY - 8 * stepPx);
    ctx.quadraticCurveTo(padL - 17, (trebleBaseY - 8 * stepPx + bassBaseY) * 0.5, padL - 12, bassBaseY);
    ctx.stroke();

    ctx.fillStyle = COLOR_SECONDARY;
    ctx.font = "italic 13px Georgia, serif";
    ctx.fillText("G", 11, trebleBaseY - stepPx * 2);
    ctx.fillText("F", 11, bassBaseY - stepPx * 3);

    // Draw Staff noteheads, ledger lines, stems & Piano Roll bars
    const rollMinMidi = 26;
    const rollMaxMidi = 92;
    let lastStemX = -999;
    for (const n of piece.notes || []) {
      const t0 = Number(n.t);
      const d = Number(n.d);
      const p = Number(n.p);
      const part = n.h === "human" ? "human" : (n.h === "lh" ? "lh" : "rh");
      const voice = n.r || "";
      const audible = isPartAudible(part);
      const isSounding = audible && t0 <= playheadS && playheadS < t0 + d;

      const x = padL + (t0 / dur) * drawW;
      const bw = Math.max(2, (d / dur) * drawW);
      const stp = midiToStaffStep(p);

      let staffY;
      let isTreble = p >= 60 && part !== "lh";
      if (isTreble) {
        staffY = trebleBaseY - (stp - 2) * stepPx;
        staffY = Math.max(staffTop + 22, Math.min(trebleBaseY + stepPx * 3.5, staffY));
      } else {
        staffY = bassBaseY - (stp - (-10)) * stepPx;
        staffY = Math.max(trebleBaseY + stepPx * 4, Math.min(staffTop + staffH - 4, staffY));
      }

      const baseColor = !audible
        ? "#2D2B27"
        : (part === "human" ? "#C5A880" : (part === "lh" ? "#7C776E" : COLOR_ADOPTED));
      const color = isSounding ? COLOR_VERMILION : baseColor;

      // Subtle ledger lines for middle C (stp=0), high treble (>=12), or low bass (<=-12)
      if (isTreble && (stp === 0 || stp >= 12)) {
        ctx.strokeStyle = "#3B3831";
        ctx.lineWidth = 0.85;
        ctx.beginPath();
        ctx.moveTo(x - 5, Math.round(staffY) + 0.5);
        ctx.lineTo(x + 5, Math.round(staffY) + 0.5);
        ctx.stroke();
      } else if (!isTreble && (stp === 0 || stp <= -12)) {
        ctx.strokeStyle = "#3B3831";
        ctx.lineWidth = 0.85;
        ctx.beginPath();
        ctx.moveTo(x - 5, Math.round(staffY) + 0.5);
        ctx.lineTo(x + 5, Math.round(staffY) + 0.5);
        ctx.stroke();
      }

      // Delicate note stem for primary melody or bass anchor notes when spaced cleanly
      if ((voice === "rh_melody" || voice === "human_melody" || voice === "lh_bass") && d >= 0.25 && Math.abs(x - lastStemX) >= 5) {
        ctx.strokeStyle = color;
        ctx.lineWidth = 0.85;
        ctx.beginPath();
        if (part === "lh") {
          ctx.moveTo(x - 2.3, staffY);
          ctx.lineTo(x - 2.3, Math.min(staffTop + staffH - 2, staffY + 10));
        } else {
          ctx.moveTo(x + 2.3, staffY);
          ctx.lineTo(x + 2.3, Math.max(staffTop + 18, staffY - 10));
        }
        ctx.stroke();
        lastStemX = x;
      }

      // Staff notehead
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.ellipse(x, staffY, 2.7, 1.9, -0.2, 0, Math.PI * 2);
      ctx.fill();

      // Piano Roll note bar
      const normY = 1 - Math.max(0, Math.min(1, (p - rollMinMidi) / (rollMaxMidi - rollMinMidi)));
      const ry = rollTop + normY * (rollH - 4);
      ctx.fillStyle = color;
      ctx.fillRect(x, ry, bw, 3.2);
    }

    // Piano Roll divider + label
    ctx.strokeStyle = "#23211D";
    ctx.beginPath();
    ctx.moveTo(padL - 8, rollTop);
    ctx.lineTo(w - padR, rollTop);
    ctx.stroke();
    ctx.fillStyle = COLOR_REJECTED;
    ctx.font = "9px 'Cascadia Mono', monospace";
    ctx.fillText("ROLL", 8, rollTop + 12);
    ctx.fillText("64/67", 6, pedBot - 2);

    // Dual Pedal Strip
    const pedH = Math.max(14, pedBot - pedTop);
    const drawPedalCurve = (pts, color, dashed) => {
      if (!pts || pts.length < 2) return;
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.25;
      if (dashed) ctx.setLineDash([4, 2]);
      ctx.beginPath();
      pts.forEach((pt, idx) => {
        const px = padL + Math.min(1, Number(pt.t) / dur) * drawW;
        const py = pedBot - (Number(pt.val) / 127.0) * (pedH - 2);
        if (idx === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.stroke();
      ctx.restore();
    };
    drawPedalCurve(piece.cc64, state.mixer.cc64 ? COLOR_ADOPTED : "#34322D", false);
    drawPedalCurve(piece.cc67, state.mixer.cc67 ? COLOR_VERMILION : "#4B2A24", true);

    // Playhead line
    const phX = padL + Math.min(1, Math.max(0, playheadS / dur)) * drawW;
    ctx.strokeStyle = COLOR_VERMILION;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(phX, staffTop);
    ctx.lineTo(phX, pedBot);
    ctx.stroke();
  }

  /* ---- Deliberation Replay ---- */
  function renderDeliberationCards(piece, activeRejectId, rejectedSet, adoptedLocked) {
    const delib = piece.deliberation || {};
    el("delib-monologue").textContent = delib.monologue_ja || piece.composer_note_ja || "--";
    const grid = el("delib-grid");
    grid.innerHTML = "";

    const allCards = [];
    if (delib.adopted) {
      allCards.push({ ...delib.adopted, isAdopted: true });
    }
    for (const r of delib.rejected || []) {
      allCards.push({ ...r, isAdopted: false });
    }

    for (const c of allCards) {
      const card = document.createElement("article");
      let cls = "cand-card ";
      if (c.isAdopted) cls += adoptedLocked ? "adopted" : "";
      else if (activeRejectId === c.id) cls += "auditioning";
      else if (rejectedSet.has(c.id)) cls += "rejected";
      card.className = cls;

      const head = document.createElement("div");
      head.className = "cand-head";
      const title = document.createElement("span");
      title.className = "cand-title";
      const firstTex = piece.acts && piece.acts[0] ? formatTextureJa(piece.acts[0].lh_texture) : "";
      const cleanLabel = c.isAdopted && firstTex ? `案A — ${firstTex}・4幕多声構成` : c.label_ja;
      title.textContent = `${cleanLabel} (${c.score}点)`;
      const tag = document.createElement("span");
      tag.className = c.isAdopted ? "cand-tag ok" : "cand-tag";
      tag.textContent = c.isAdopted ? (adoptedLocked ? "採用 LOCKED" : "評価中") : c.tag_ja;
      head.append(title, tag);

      const cvs = document.createElement("canvas");
      cvs.className = "mini-canvas";
      card.append(head, cvs);
      grid.appendChild(card);

      // Draw mini staff notes
      requestAnimationFrame(() => {
        const rect = cvs.getBoundingClientRect();
        const cw = Math.max(180, Math.floor(rect.width));
        const ch = 68;
        cvs.width = cw;
        cvs.height = ch;
        const cctx = cvs.getContext("2d");
        cctx.fillStyle = COLOR_STAGE;
        cctx.fillRect(0, 0, cw, ch);
        cctx.strokeStyle = "#22201C";
        for (let l = 0; l < 5; l++) {
          const y = 14 + l * 8;
          cctx.beginPath();
          cctx.moveTo(8, y);
          cctx.lineTo(cw - 8, y);
          cctx.stroke();
        }
        const nList = c.notes || [];
        const maxT = Math.max(1.5, ...nList.map((n) => Number(n.t) + Number(n.d || 0.3)));
        nList.forEach((n) => {
          const nx = 14 + (Number(n.t) / maxT) * (cw - 28);
          const ny = Math.max(8, Math.min(ch - 8, ch - ((Number(n.p) - 36) / 52) * (ch - 14)));
          cctx.fillStyle = c.isAdopted ? COLOR_ADOPTED : (activeRejectId === c.id ? COLOR_VERMILION : "#7D786E");
          cctx.beginPath();
          cctx.ellipse(nx, ny, 3.2, 2.3, -0.2, 0, Math.PI * 2);
          cctx.fill();
        });
        if (!c.isAdopted && rejectedSet.has(c.id) && activeRejectId !== c.id) {
          cctx.strokeStyle = "rgba(200, 62, 43, 0.72)";
          cctx.lineWidth = 1.5;
          cctx.beginPath();
          cctx.moveTo(12, 12);
          cctx.lineTo(cw - 12, ch - 12);
          cctx.stroke();
        }
      });
    }
  }

  function replayDeliberation() {
    if (!state.currentPiece) return;
    switchWorkspaceTab("tab-delib");
    state.delibTimers.forEach((t) => clearTimeout(t));
    state.delibTimers = [];
    const rejIds = (state.currentPiece.deliberation?.rejected || []).map((r) => r.id);
    const rejSet = new Set();
    renderDeliberationCards(state.currentPiece, "", rejSet, false);

    rejIds.forEach((rid, idx) => {
      const tid = setTimeout(() => {
        rejSet.add(rid);
        renderDeliberationCards(state.currentPiece, rid, rejSet, false);
      }, 280 + idx * 340);
      state.delibTimers.push(tid);
    });

    const finTid = setTimeout(() => {
      renderDeliberationCards(state.currentPiece, "", rejSet, true);
    }, 280 + rejIds.length * 340 + 260);
    state.delibTimers.push(finTid);
  }

  /* ---- Piece Loading & UI Updates ---- */
  async function loadPiece(pieceId) {
    stopPlayback(true);
    try {
      const res = await fetch(pieceUrl(pieceId));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const piece = await res.json();
      state.currentPiece = piece;
      state.playback.durationS = Number(piece.duration_s || 36);
      state.playback.offsetS = 0;

      // Update Sidebar selection highlight & scroll active item gently into view
      document.querySelectorAll(".piece-item").forEach((btn) => {
        const sel = btn.dataset.pieceId === pieceId;
        btn.classList.toggle("selected", sel);
        if (sel) {
          btn.scrollIntoView({ block: "nearest" });
        }
      });

      el("work-title").textContent = `${piece.id.toUpperCase()} — ${piece.title_ja}`;
      el("work-subtitle").textContent = `${piece.title_en} | ${piece.mode_label_ja} | ${piece.tonality_ja} | ${piece.bpm} BPM | ${piece.duration_s}秒 | ${piece.llm_source}`;
      el("xray-tonality").textContent = `${piece.tonality_ja} / ${piece.bpm} BPM`;
      el("human-part-label").textContent = piece.mode === "cello" ? "チェロ旋律 (Mode B)" : "ピアノ I 対話 (Mode A)";
      el("composer-note-full").textContent = `作曲設計メモ (${piece.llm_source}): ${piece.composer_note_ja || ""}`;

      const au = piece.audit || {};
      el("xray-consonance").textContent = `${au.consonance_score} / 100`;
      el("xray-polyphony").textContent = `${au.avg_polyphony} 音`;
      el("xray-mono").textContent = `${au.mono_ratio_pct}%`;

      // Progression chips
      const progWrap = el("xray-progression");
      progWrap.innerHTML = "";
      for (const seg of piece.chord_timeline || []) {
        const chip = document.createElement("div");
        chip.className = "chord-chip";
        chip.textContent = `${seg.chord}`;
        chip.title = `${seg.bar}小節: ${seg.chord} (${seg.roman}) — ${seg.function_ja}`;
        progWrap.appendChild(chip);
      }

      // 5-Axis Audit Bars
      const axes = [
        ["協和率", au.consonance_score],
        ["多声性", au.polyphony_score],
        ["歌唱性", au.melody_cantabile_score],
        ["和声引力", au.harmonic_progression_score],
        ["ペダル", au.pedal_cleanliness_score],
      ];
      const auditWrap = el("audit-bars");
      auditWrap.innerHTML = "";
      for (const [label, val] of axes) {
        const item = document.createElement("div");
        item.className = "audit-bar-item";
        item.innerHTML = `<div style="display:flex;justify-content:space-between;"><span>${label}</span><span>${val}</span></div><div class="audit-bar-bg"><div class="audit-bar-fill" style="width:${Math.round(val)}%"></div></div>`;
        auditWrap.appendChild(item);
      }

      // MP3 Player wiring (auto-load showcase MP3 if available, else fallback to top Mode-matching showcase)
      const mp3El = el("mp3-player");
      if (mp3El) {
        let targetMp3 = piece.showcase_mp3;
        let mp3Desc = "";
        if (targetMp3) {
          mp3Desc = `本曲のホール録音MP3:`;
        } else {
          const fallbackEntry = (state.catalog?.pieces || []).find(
            (e) => e.showcase_mp3 && e.mode === piece.mode
          );
          if (fallbackEntry) {
            targetMp3 = fallbackEntry.showcase_mp3;
            mp3Desc = `代表曲ホール録音MP3 (${fallbackEntry.id}):`;
          }
        }
        el("mp3-label").textContent = mp3Desc || "ホール録音MP3:";
        if (targetMp3) {
          const nextSrc = audioAssetUrl(targetMp3);
          if (mp3El.getAttribute("src") !== nextSrc) {
            mp3El.setAttribute("src", nextSrc);
            mp3El.load();
          }
        }
      }

      renderDeliberationCards(piece, "", new Set((piece.deliberation?.rejected || []).map((r) => r.id)), true);
      updateRealtimeTelemetry(0);
      drawScoreCanvas(0);
    } catch (err) {
      console.error("Failed to load piece", pieceId, err);
    }
  }

  function applyFilters() {
    const pieces = state.catalog?.pieces || [];
    const q = (state.filterSearch || "").trim().toLowerCase();
    state.filtered = pieces.filter((p) => {
      if (state.filterMode === "showcase" && !p.showcase_mp3) return false;
      if (state.filterMode === "cello" && p.mode !== "cello") return false;
      if (state.filterMode === "piano" && p.mode !== "piano") return false;
      if (state.filterTonality !== "all" && p.tonality !== state.filterTonality) return false;
      if (Number(p.beauty_score) < Number(state.filterMinScore || 0)) return false;
      if (q) {
        const hay = `${p.id} ${p.title_ja} ${p.title_en} ${p.tonality_ja} ${(p.chords_preview || []).join(" ")}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    el("filtered-count").textContent = `${state.filtered.length} / ${pieces.length}`;
    const list = el("piece-list");
    list.innerHTML = "";
    const curId = state.currentPiece?.id;

    for (const p of state.filtered) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "piece-item" + (p.id === curId ? " selected" : "");
      btn.dataset.pieceId = p.id;
      const modeShort = p.mode === "cello" ? "Mode B (Vc+Pf)" : "Mode A (Pf+Pf)";
      btn.innerHTML = `
        <div class="piece-item-top">
          <span class="piece-id-tag">#${String(p.index).padStart(3, "0")} | ${modeShort}</span>
          ${p.showcase_mp3 ? '<span class="mp3-tag">MP3</span>' : `<span class="piece-id-tag">${p.beauty_score}点</span>`}
        </div>
        <div class="piece-title">${p.title_ja}</div>
        <div class="piece-meta">
          <span>${p.tonality_ja} (${p.bpm}BPM)</span>
          <span>協和${p.consonance_score}% / ${p.avg_polyphony}音</span>
        </div>
      `;
      btn.addEventListener("click", () => loadPiece(p.id));
      list.appendChild(btn);
    }
  }

  function switchWorkspaceTab(tabId) {
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      const active = btn.id === tabId;
      btn.setAttribute("aria-selected", active ? "true" : "false");
      const panelId = btn.getAttribute("aria-controls");
      const panel = panelId ? el(panelId) : null;
      if (panel) panel.hidden = !active;
    });
    if (tabId === "tab-score") {
      requestAnimationFrame(() => drawScoreCanvas(currentPlaybackTimeS()));
    }
  }

  async function initLiveControls() {
    if (!LIVE_MODE) return;
    const chip = el("audio-engine-chip");
    async function refreshStageState() {
      try {
        const r = await fetch("/api/stage/state");
        if (!r.ok) return;
        const st = await r.json();
        if (chip) {
          chip.textContent = st.production_mode ? "本番実機モード (--production)" : `ライブ接続済 (Turn #${st.turn_id || 0})`;
        }
        el("live-mode-cello")?.classList.toggle("active", st.mode === "cello");
        el("live-mode-piano")?.classList.toggle("active", st.mode === "piano");
        const demoBtn = el("live-demo-toggle");
        if (demoBtn) {
          demoBtn.textContent = st.demo_enabled ? "仮想デモ: ON" : "仮想デモ: OFF";
          demoBtn.classList.toggle("active", !!st.demo_enabled);
        }
        if (st.intent?.monologue_ja) {
          el("live-monologue-box").textContent = `Turn #${st.turn_id}: ${st.intent.monologue_ja}`;
        }
      } catch (_) {}
    }

    async function postApi(path, body) {
      el("live-status-text").textContent = "実行中...";
      const r = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : "{}",
      });
      if (r.ok) {
        el("live-status-text").textContent = "完了";
        await refreshStageState();
      } else {
        el("live-status-text").textContent = `HTTP ${r.status}`;
      }
    }

    el("live-mode-cello")?.addEventListener("click", () => postApi("/api/stage/mode", { mode: "cello" }));
    el("live-mode-piano")?.addEventListener("click", () => postApi("/api/stage/mode", { mode: "piano" }));
    el("live-demo-toggle")?.addEventListener("click", async () => {
      const r = await fetch("/api/stage/state");
      const st = await r.json();
      await postApi("/api/stage/demo", { enabled: !st.demo_enabled });
    });
    el("live-next-turn")?.addEventListener("click", () => postApi("/api/stage/turn"));
    el("live-retake")?.addEventListener("click", () =>
      postApi("/api/stage/retake", { text: el("live-directive-input")?.value || "" })
    );
    el("live-synth-work")?.addEventListener("click", () => postApi("/api/stage/synthesize_work", { play_audio: false }));
    el("live-calibrate")?.addEventListener("click", () => postApi("/api/stage/calibrate"));
    el("live-directive-send")?.addEventListener("click", () =>
      postApi("/api/stage/directive", { text: el("live-directive-input")?.value || "" })
    );

    const params = new URLSearchParams(window.location.search);
    if (params.get("demo") === "1") {
      fetch("/api/stage/demo", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: true }) }).catch(() => {});
    }
    refreshStageState();
  }

  async function boot() {
    // Wire tabs
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => switchWorkspaceTab(btn.id));
    });

    // Wire filters
    el("filter-pills").querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        el("filter-pills").querySelectorAll("button").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        state.filterMode = btn.dataset.filter || "all";
        applyFilters();
      });
    });
    el("filter-tonality").addEventListener("change", (ev) => {
      state.filterTonality = ev.target.value;
      applyFilters();
    });
    el("filter-score").addEventListener("change", (ev) => {
      state.filterMinScore = Number(ev.target.value || 0);
      applyFilters();
    });
    el("filter-search").addEventListener("input", (ev) => {
      state.filterSearch = ev.target.value || "";
      applyFilters();
    });

    // Wire mixer buttons
    document.querySelectorAll("[data-mix-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.dataset.mixToggle;
        state.mixer[key] = !state.mixer[key];
        btn.classList.toggle("active", state.mixer[key]);
        btn.textContent = state.mixer[key] ? "ON" : "OFF";
        applyMixerGains();
        drawScoreCanvas(currentPlaybackTimeS());
      });
    });
    document.querySelectorAll("[data-mix-solo]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.dataset.mixSolo;
        state.mixer.solo = state.mixer.solo === key ? null : key;
        document.querySelectorAll("[data-mix-solo]").forEach((b) => {
          b.classList.toggle("vermilion", b.dataset.mixSolo === state.mixer.solo);
          b.classList.toggle("active", b.dataset.mixSolo === state.mixer.solo);
        });
        applyMixerGains();
        drawScoreCanvas(currentPlaybackTimeS());
      });
    });
    el("btn-mixer-reset").addEventListener("click", () => {
      state.mixer = { rh: true, lh: true, human: true, cc64: true, cc67: true, solo: null };
      document.querySelectorAll("[data-mix-toggle]").forEach((b) => {
        b.classList.add("active");
        b.textContent = "ON";
      });
      document.querySelectorAll("[data-mix-solo]").forEach((b) => {
        b.classList.remove("vermilion", "active");
      });
      applyMixerGains();
      drawScoreCanvas(currentPlaybackTimeS());
    });

    // Playback & Deck buttons
    el("btn-play").addEventListener("click", () => {
      if (state.playback.playing) stopPlayback(false);
      else startPlayback();
    });
    el("btn-panic").addEventListener("click", () => {
      stopPlayback(true);
      const mp3El = el("mp3-player");
      if (mp3El) {
        mp3El.pause();
        mp3El.currentTime = 0;
      }
      if (LIVE_MODE) {
        fetch("/api/stage/panic", { method: "POST" }).catch(() => {});
      }
    });
    el("btn-download-midi").addEventListener("click", downloadCurrentMidi);
    el("btn-replay-delib").addEventListener("click", replayDeliberation);

    el("seek-slider").addEventListener("input", (ev) => {
      if (!state.currentPiece) return;
      const frac = Number(ev.target.value || 0) / 1000.0;
      const targetS = frac * Number(state.currentPiece.duration_s || 36);
      if (state.playback.playing) startPlayback(targetS);
      else {
        state.playback.offsetS = targetS;
        updateRealtimeTelemetry(targetS);
        drawScoreCanvas(targetS);
      }
    });

    el("canvas-studio-score").addEventListener("click", (ev) => {
      if (!state.currentPiece) return;
      const rect = ev.currentTarget.getBoundingClientRect();
      const frac = Math.max(0, Math.min(1, (ev.clientX - rect.left - 42) / Math.max(100, rect.width - 56)));
      const targetS = frac * Number(state.currentPiece.duration_s || 36);
      if (state.playback.playing) startPlayback(targetS);
      else {
        state.playback.offsetS = targetS;
        updateRealtimeTelemetry(targetS);
        drawScoreCanvas(targetS);
      }
    });

    window.addEventListener("resize", () => {
      drawScoreCanvas(currentPlaybackTimeS());
    });

    // Keyboard shortcuts (Space play/pause, Left/Right seek +-3s, Esc stop)
    window.addEventListener("keydown", (ev) => {
      const tag = (ev.target && ev.target.tagName) ? ev.target.tagName.toUpperCase() : "";
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
      if (ev.code === "Space" && state.currentPiece) {
        ev.preventDefault();
        if (state.playback.playing) stopPlayback(false);
        else startPlayback();
      } else if (ev.code === "ArrowLeft" && state.currentPiece) {
        ev.preventDefault();
        const t = Math.max(0, currentPlaybackTimeS() - 3.0);
        if (state.playback.playing) startPlayback(t);
        else {
          state.playback.offsetS = t;
          updateRealtimeTelemetry(t);
          drawScoreCanvas(t);
        }
      } else if (ev.code === "ArrowRight" && state.currentPiece) {
        ev.preventDefault();
        const dur = Math.max(1, Number(state.currentPiece.duration_s || 36));
        const t = Math.min(dur - 0.1, currentPlaybackTimeS() + 3.0);
        if (state.playback.playing) startPlayback(t);
        else {
          state.playback.offsetS = t;
          updateRealtimeTelemetry(t);
          drawScoreCanvas(t);
        }
      } else if (ev.code === "Escape") {
        stopPlayback(true);
      }
    });

    // Load 100-piece catalog
    try {
      const res = await fetch(catalogUrl());
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const catalog = await res.json();
      state.catalog = catalog;
      const st = catalog.stats || {};
      el("catalog-stats-pill").textContent =
        `${st.total_pieces || 100}曲 (平均美しさ ${st.avg_beauty_score || "--"} / 協和 ${st.avg_consonance_score || "--"}% / 同時 ${st.avg_polyphony || "--"}音)`;

      // Populate tonality dropdown
      const seenTons = new Map();
      for (const p of catalog.pieces || []) {
        if (!seenTons.has(p.tonality)) seenTons.set(p.tonality, p.tonality_ja);
      }
      const tonSel = el("filter-tonality");
      for (const [k, v] of seenTons.entries()) {
        const opt = document.createElement("option");
        opt.value = k;
        opt.textContent = v;
        tonSel.appendChild(opt);
      }

      applyFilters();
      const firstId = (catalog.showcase_ids && catalog.showcase_ids[0]) || (catalog.pieces?.[0]?.id);
      if (firstId) await loadPiece(firstId);
    } catch (err) {
      el("catalog-stats-pill").textContent = "カタログ読込エラー";
      console.error("Failed to load catalog", err);
    }

    initLiveControls();
  }

  window.addEventListener("DOMContentLoaded", boot);
})();
