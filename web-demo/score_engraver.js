/**
 * Disklavier-Auto VexFlow 4.2.5 (Bravura) Grand Staff Engraving Engine & Shared Terminology Formatter.
 *
 * Enforces Henle / Baerenreiter engraving rules on dark editorial backgrounds (#1C1D20 / #141517):
 * - True Treble + Bass Grand Staff with left Brace, barlines, key signature, & aligned startX
 * - Key-aware enharmonic spelling & independent Treble/Bass bar accidentals
 * - Adaptive 16th/8th/quarter beat quantization guaranteed to sum to exact 4/4 measure capacity
 * - Automatic stem directions, chord stems, 2nd-interval head offsets, & beat-grouped beams
 * - Strict zero-overlap dynamic Y-lanes:
 *     Lane 1 (Above Treble): Measure numbers + Chord symbols
 *     Lane 2 (Inter-Stave):  Dynamic markings (pp / mp / mf / f)
 *     Lane 3 (Below Bass):   Left Una Corda (CC#67) & Right Damper (CC#64) pedal brackets
 *     Lane 4 (Bottom):       Rejected candidate vermilion markers + reason lane
 * - 60fps Playhead overlay & measure-accurate click-to-seek without re-engraving
 */
(() => {
  "use strict";

  // =========================================================================
  // Shared Japanese & Musical Notation Formatters (Zero Raw Identifiers)
  // =========================================================================
  const TONALITY_JA_MAP = {
    C_major: "ハ長調",
    C: "ハ長調",
    G_major: "ト長調",
    G: "ト長調",
    D_major: "ニ長調",
    D: "ニ長調",
    A_major: "イ長調",
    A: "イ長調",
    E_major: "ホ長調",
    E: "ホ長調",
    F_major: "ヘ長調",
    F: "ヘ長調",
    Bb_major: "変ロ長調",
    Bb: "変ロ長調",
    Eb_major: "変ホ長調",
    Eb: "変ホ長調",
    Ab_major: "変イ長調",
    Ab: "変イ長調",
    A_minor: "イ短調",
    Am: "イ短調",
    E_minor: "ホ短調",
    Em: "ホ短調",
    B_minor: "ロ短調",
    Bm: "ロ短調",
    "F#_minor": "嬰ヘ短調",
    "F#m": "嬰ヘ短調",
    D_minor: "ニ短調",
    Dm: "ニ短調",
    D_dorian: "ニ・ドリア旋法",
    Ddor: "ニ・ドリア旋法",
    G_minor: "ト短調",
    Gm: "ト短調",
    C_minor: "ハ短調",
    Cm: "ハ短調",
    F_minor: "ヘ短調",
    Fm: "ヘ短調",
  };

  const PHRASE_FUNCTION_JA = {
    question_half_cadence: "半終止（問いかけ）",
    dialogic_sequence: "模進展開（対話）",
    polyphonic_climax: "多声頂点（高揚）",
    recapitulation_resolution: "回想・完全終止",
    antecedent_open: "前楽節・提示",
    antecedent_half_cadence: "前楽節・半終止",
    consequent_develop: "後楽節・展開",
    consequent_apex: "後楽節・頂点",
    consequent_cadence: "後楽節・完全終止",
  };

  const ENGINE_JA_MAP = {
    auto: "自動最適選択",
    notagen_small: "NotaGen-Small（量子化）",
    anticipatory_transformer: "Anticipatory Transformer (128M)",
    midigpt_cpu: "MIDI-GPT 伴奏エンジン (CPU)",
    ollama_abc_stream: "ローカルLLM (Ollama逐次生成)",
    llm_json: "ローカルLLM (和声設計)",
    transformer_abc_fallback: "軽量Transformer",
    browser_abc_demo: "ブラウザWebAudio生成",
    webaudio_demo: "ブラウザWebAudioデモ",
    rule_fallback: "和声規則エンジン",
    mac_local: "Macローカル",
    mac_local_failover: "Macローカル退避中",
    remote_rtx: "自宅RTX 3070 (外部)",
    heuristic: "対位法規則生成",
  };

  const ARTICULATION_JA_MAP = {
    legato: "レガート",
    cantabile: "カンタービレ",
    Cantabile: "カンタービレ",
    staccato: "スタッカート",
    spiccato: "スピッカート",
    marcato: "マルカート",
    tenuto: "テヌート",
    pizzicato: "ピッツィカート",
  };

  const TURN_REASON_JA_MAP = {
    virtual_cello_phrase_end: "フレーズ終止（仮想チェロ）",
    virtual_human_phrase_end: "フレーズ終止（仮想演奏）",
    silence_breath: "呼吸・休止検知",
    phrase_end: "フレーズ終止検知",
    cadence_pause: "終止休止検知",
    manual_turn: "手動ターン実行",
    listening: "聴取待機中",
    pedal_cue: "左ペダル合図検知",
    retake: "別解釈を再計算",
  };

  const STANCE_JA_MAP = {
    imitate: "模倣・追従",
    counterpoint: "対位法対話",
    cadence: "終止接続",
    provoke: "和声的挑発",
    echo_support: "持続和声支え",
    resolve: "解決導出",
  };

  const PEDAL_STRATEGY_JA_MAP = {
    half_clear: "ハーフペダル清澄踏み替え",
    resonant: "豊かな共鳴保持",
    dry: "ドライ（ペダル抑制）",
    una_corda_veil: "ウナ・コルダ弱音ベール",
    repedal_sync: "和声境界同期踏み替え",
  };

  const TEXTURE_JA_MAP = {
    broken_chord_barcarolle: "分散和音・バルカローレ",
    cantabile_polyphony: "カンタービレ多声対位",
    impressionist_pedal: "印象派・半ペダル共鳴",
    dramatic_octave_chorale: "重厚コラール・オクターヴ",
    minimalist_pulse: "静謐反復アルペジオ",
    countermelody_tenor: "内声テノール対旋律",
    open_arpeggio_10th: "開離アルペジオ",
    chorale_voicing: "コラール四声和音",
    alberti_lyrical: "叙情アルベルティ伴奏",
    pedal_point_inner_voice: "保続低音と内声対話",
    syncopated_offbeat_harmony: "シンコペーション和声",
    una_corda_choral_recall: "ウナ・コルダ回想コラール",
    arpeggio_root_5th_10th: "根音・五度・十度アルペジオ",
  };

  function formatChordSymbol(raw) {
    const s = String(raw || "").trim();
    if (!s || s === "--") return "--";
    return s
      .replace(/([A-G])b/g, "$1♭")
      .replace(/([A-G])#/g, "$1♯")
      .replace(/([0-9])b([0-9])/g, "$1♭$2")
      .replace(/([0-9])#([0-9])/g, "$1♯$2")
      .replace(/\/([A-G])b/g, "/$1♭")
      .replace(/\/([A-G])#/g, "/$1♯");
  }

  function formatNoteName(raw) {
    const s = String(raw || "").trim();
    if (!s || s === "--") return "--";
    return s.replace(/([A-G])b(?=-?\d|$)/g, "$1♭").replace(/([A-G])#(?=-?\d|$)/g, "$1♯");
  }

  function formatMusicText(raw) {
    let s = String(raw || "");
    if (!s) return "";
    // Replace known raw key/stance/voicing/pedal tokens cleanly before musical symbol formatting
    for (const [k, v] of Object.entries(TONALITY_JA_MAP)) {
      if (k.includes("_")) s = s.replaceAll(k, v);
    }
    for (const [k, v] of Object.entries(STANCE_JA_MAP)) {
      s = s.replace(new RegExp(`\\b${k}\\b`, "g"), v);
    }
    for (const [k, v] of Object.entries(PEDAL_STRATEGY_JA_MAP)) {
      s = s.replace(new RegExp(`\\b${k}\\b`, "g"), v);
    }
    s = s
      .replace(/LH_root_5th_10th_arpeggio/g, "左手根音・5度・10度分散")
      .replace(/LH_open5th_10th/g, "左手開離5度・10度")
      .replace(/LH_open5th/g, "左手開離5度")
      .replace(/RH_cantabile_9th_spread/g, "右手歌謡9度開離")
      .replace(/RH_cantabile/g, "右手歌謡旋律");
    // Replace note names and chords like Bb3, C#4, Bbmaj9, F#m7, Gm6/Bb cleanly
    s = s.replace(/\b([A-G])b((?:maj|min|m|dim|aug|sus|add)?\d*(?:b\d+|#\d+)?(?:\/[A-G][b#]?)?)\b/g, "$1♭$2");
    s = s.replace(/\b([A-G])#((?:maj|min|m|dim|aug|sus|add)?\d*(?:b\d+|#\d+)?(?:\/[A-G][b#]?)?)\b/g, "$1♯$2");
    s = s.replace(/\/([A-G])b\b/g, "/$1♭").replace(/\/([A-G])#\b/g, "/$1♯");
    s = s.replace(/([0-9])b([0-9])/g, "$1♭$2").replace(/([0-9])#([0-9])/g, "$1♯$2");
    return s;
  }

  function formatTonalityJa(tonality) {
    const k = String(tonality || "").trim();
    if (!k) return "ニ短調";
    if (TONALITY_JA_MAP[k]) return TONALITY_JA_MAP[k];
    return formatMusicText(k.replace(/_/g, " "));
  }

  function formatPhraseFunctionJa(fn) {
    const k = String(fn || "").trim();
    if (!k) return "半終止（問いかけ）";
    if (PHRASE_FUNCTION_JA[k]) return PHRASE_FUNCTION_JA[k];
    return k.replace(/_/g, " ");
  }

  function formatEngineJa(backend) {
    const k = String(backend || "auto").trim();
    if (ENGINE_JA_MAP[k]) return ENGINE_JA_MAP[k];
    if (k.startsWith("ollama:")) return `Ollama (${k.slice(7)})`;
    return k.replace(/_/g, " ");
  }

  function formatArticulationJa(art) {
    const k = String(art || "").trim();
    return ARTICULATION_JA_MAP[k] || k || "静観";
  }

  function formatTurnReasonJa(reason) {
    const k = String(reason || "").trim();
    return TURN_REASON_JA_MAP[k] || k.replace(/_/g, " ") || "待機中";
  }

  function formatStanceJa(stance) {
    const k = String(stance || "").trim();
    return STANCE_JA_MAP[k] || k.replace(/_/g, " ") || "対話";
  }

  function formatPedalStrategyJa(strat) {
    const k = String(strat || "").trim();
    return PEDAL_STRATEGY_JA_MAP[k] || k.replace(/_/g, " ") || "ハーフペダル";
  }

  function formatTextureJa(tex) {
    const k = String(tex || "").trim();
    return TEXTURE_JA_MAP[k] || k.replace(/_/g, " ");
  }

  function formatVoicingJa(voicing) {
    const s = String(voicing || "").trim();
    if (!s) return "両手開離ボイシング";
    return s
      .replace(/LH_open5th_10th/g, "左手開離5度・10度")
      .replace(/LH_open5th/g, "左手開離5度")
      .replace(/LH_root_5th_10th_arpeggio/g, "左手根音・5度・10度分散")
      .replace(/RH_cantabile_9th_spread/g, "右手歌謡9度開離")
      .replace(/RH_cantabile/g, "右手歌謡旋律")
      .replace(/_/g, " ");
  }

  function formatCompactContextJa(raw) {
    let s = String(raw || "").trim();
    if (!s) return "圧縮文脈なし";
    s = s
      .replace(/^IN\[cello\|/, "入力[チェロ|")
      .replace(/^IN\[piano\|/, "入力[ピアノ|")
      .replace(/\|legato\|/g, "|レガート|")
      .replace(/\|staccato\|/g, "|スタッカート|")
      .replace(/\|cantabile\|/g, "|カンタービレ|")
      .replace(/\|N=/g, "|音列=")
      .replace(/\|IV=/g, "|音程=")
      .replace(/\|T=/g, "|緊張=")
      .replace(/\|M=/g, "|勢い=")
      .replace(/\|AVOID=/g, "|回避帯=")
      .replace(/FORM\[Act=(\d+)\(([^)]+)\)\|Key=([^|]+)\|Seed=/g, "構成[第$1幕|調性=$3|動機=")
      .replace(/\|Br=/g, "|接続=")
      .replace(/\|Apex=/g, "|頂点=")
      .replace(/\|HIST=/g, "|直近進行=")
      .replace(/CORPUS=V\(/g, "参照コーパス=(")
      .replace(/RULE=/g, "規則=")
      .replace(/no_scale_run>=4/g, "音階連続4音未満抑制")
      .replace(/require_2hand_polyphony/g, "両手多声必須")
      .replace(/post_leap_gap_fill/g, "跳躍後順次充填")
      .replace(/dual_pedal/g, "左右ペダル統合");
    return formatMusicText(s);
  }

  // =========================================================================
  // Tonality -> Key Signature & Key-Aware Enharmonic Pitch Spelling
  // =========================================================================
  const TONALITY_KEYSIG = {
    C_major: "C",
    C: "C",
    G_major: "G",
    G: "G",
    D_major: "D",
    D: "D",
    A_major: "A",
    A: "A",
    E_major: "E",
    E: "E",
    F_major: "F",
    F: "F",
    Bb_major: "Bb",
    Bb: "Bb",
    Eb_major: "Eb",
    Eb: "Eb",
    Ab_major: "Ab",
    Ab: "Ab",
    A_minor: "Am",
    Am: "Am",
    E_minor: "Em",
    Em: "Em",
    B_minor: "Bm",
    Bm: "Bm",
    "F#_minor": "F#m",
    "F#m": "F#m",
    D_minor: "Dm",
    Dm: "Dm",
    D_dorian: "C",
    Ddor: "C",
    G_minor: "Gm",
    Gm: "Gm",
    C_minor: "Cm",
    Cm: "Cm",
    F_minor: "Fm",
    Fm: "Fm",
  };

  // Accurate 12-pitch-class spelling tables per key signature (Rule 4)
  // pc: 0=C, 1=C#/Db, 2=D, 3=D#/Eb, 4=E, 5=F, 6=F#/Gb, 7=G, 8=G#/Ab, 9=A, 10=A#/Bb, 11=B
  const KEY_SPELLING_TABLE = {
    // 0 accidentals
    C: ["c", "c#", "d", "eb", "e", "f", "f#", "g", "g#", "a", "bb", "b"],
    Am: ["c", "c#", "d", "eb", "e", "f", "f#", "g", "g#", "a", "bb", "b"],
    // Flat keys (strictly Bb, Eb, Ab, Db, Gb; keep C# for D minor leading tone)
    F: ["c", "db", "d", "eb", "e", "f", "f#", "g", "ab", "a", "bb", "b"],
    Dm: ["c", "c#", "d", "eb", "e", "f", "f#", "g", "ab", "a", "bb", "b"],
    Bb: ["c", "db", "d", "eb", "e", "f", "f#", "g", "ab", "a", "bb", "b"],
    Gm: ["c", "db", "d", "eb", "e", "f", "f#", "g", "ab", "a", "bb", "b"],
    Eb: ["c", "db", "d", "eb", "e", "f", "gb", "g", "ab", "a", "bb", "b"],
    Cm: ["c", "db", "d", "eb", "e", "f", "gb", "g", "ab", "a", "bb", "b"],
    Ab: ["c", "db", "d", "eb", "e", "f", "gb", "g", "ab", "a", "bb", "b"],
    Fm: ["c", "db", "d", "eb", "e", "f", "gb", "g", "ab", "a", "bb", "b"],
    // Sharp keys (strictly F#, C#, G#, D#, A#)
    G: ["c", "c#", "d", "d#", "e", "f", "f#", "g", "g#", "a", "bb", "b"],
    Em: ["c", "c#", "d", "d#", "e", "f", "f#", "g", "g#", "a", "a#", "b"],
    D: ["c", "c#", "d", "d#", "e", "f", "f#", "g", "g#", "a", "a#", "b"],
    Bm: ["c", "c#", "d", "d#", "e", "f", "f#", "g", "g#", "a", "a#", "b"],
    A: ["c", "c#", "d", "d#", "e", "f", "f#", "g", "g#", "a", "a#", "b"],
    "F#m": ["c", "c#", "d", "d#", "e", "e#", "f#", "g", "g#", "a", "a#", "b"],
    E: ["c", "c#", "d", "d#", "e", "f", "f#", "g", "g#", "a", "a#", "b"],
  };

  function resolveKeySignature(tonality, chords) {
    const t = String(tonality || "").trim();
    if (TONALITY_KEYSIG[t]) return TONALITY_KEYSIG[t];
    if (Array.isArray(chords) && chords.length > 0) {
      const c0 = String(chords[0].chord || chords[0] || "").trim();
      const m = c0.match(/^([A-G][b#]?m?)/);
      if (m && TONALITY_KEYSIG[m[1]]) return TONALITY_KEYSIG[m[1]];
    }
    return "Dm";
  }

  function midiToVexKey(midi, keySig) {
    const m = Math.max(21, Math.min(108, Math.round(Number(midi) || 60)));
    const oct = Math.floor(m / 12) - 1;
    const pc = ((m % 12) + 12) % 12;
    const tbl = KEY_SPELLING_TABLE[keySig] || KEY_SPELLING_TABLE.Dm;
    let letterAcc = tbl[pc] || "c";
    let adjOct = oct;
    if (letterAcc === "e#" && pc === 5) {
      letterAcc = "e#";
    }
    return `${letterAcc}/${adjOct}`;
  }

  // =========================================================================
  // Beat Quantization & Strict 4/4 Bar Partitioner
  // =========================================================================
  const DUR_SLOTS_TO_VF = [
    [16, "w"],
    [12, "hd"],
    [8, "h"],
    [6, "qd"],
    [4, "q"],
    [3, "8d"],
    [2, "8"],
    [1, "16"],
  ];

  const REST_SLOTS_TO_VF = [
    [16, "wr"],
    [8, "hr"],
    [4, "qr"],
    [2, "8r"],
    [1, "16r"],
  ];

  function splitRestGap(gapSlots, startSlot, step) {
    const out = [];
    let cur = startSlot;
    let rem = gapSlots;
    while (rem > 0) {
      let matched = false;
      for (const [len, dur] of REST_SLOTS_TO_VF) {
        if (len < step) continue;
        if (len > rem) continue;
        // Classical engraving rule: do not cross bar midpoint (slot 8) unless whole-bar rest (start=0, len=16)
        if (len === 16 && cur !== 0) continue;
        if (len === 8 && cur !== 0 && cur !== 8) continue;
        if (len === 4 && cur % 4 !== 0) continue;
        if (len === 2 && cur % 2 !== 0) continue;
        out.push({ isRest: true, dur, ticks: len, startSlot: cur });
        cur += len;
        rem -= len;
        matched = true;
        break;
      }
      if (!matched) {
        const unit = Math.min(rem, step);
        const dur = unit >= 4 ? "qr" : unit >= 2 ? "8r" : "16r";
        out.push({ isRest: true, dur, ticks: unit, startSlot: cur });
        cur += unit;
        rem -= unit;
      }
    }
    return out;
  }

  function pickNoteDuration(availSlots, desiredSlots, curSlot, step) {
    const cap = Math.max(step, availSlots);
    const target = Math.max(step, Math.min(cap, desiredSlots));
    for (const [len, dur] of DUR_SLOTS_TO_VF) {
      if (len < step) continue;
      if (len > target) continue;
      // Prevent awkward syncopated whole/dotted-half crossing beat 3 on off-beats
      if (len === 16 && curSlot !== 0) continue;
      if (len === 12 && curSlot !== 0 && curSlot !== 4) continue;
      if (len === 8 && curSlot % 4 !== 0) continue;
      if (len === 6 && curSlot % 2 !== 0) continue;
      return { len, dur };
    }
    if (step >= 4) return { len: 4, dur: "q" };
    if (step >= 2) return { len: 2, dur: "8" };
    return { len: 1, dur: "16" };
  }

  function quantizeBarVoice(barNotes, barStartS, barEndS, clef, keySig, maxGridStep) {
    const step = maxGridStep || 1; // 1 = 16th, 2 = 8th, 4 = quarter
    const barDur = Math.max(0.25, barEndS - barStartS);
    const slotsCount = Math.round(16 / step);

    const buckets = new Map();
    for (const n of barNotes) {
      const rel = (n.t - barStartS) / barDur;
      const rawIdx = Math.max(0, Math.min(slotsCount - 1, Math.round(rel * slotsCount)));
      const slot = rawIdx * step;
      if (!buckets.has(slot)) buckets.set(slot, []);
      const list = buckets.get(slot);
      const roundedPitch = Math.round(n.p);
      const existing = list.find((item) => item.midi === roundedPitch);
      if (existing) {
        existing.dur = Math.max(existing.dur, n.d);
        if (n.h === "human") existing.hand = "human";
        if (n.problem) existing.problem = true;
      } else {
        list.push({
          midi: roundedPitch,
          dur: n.d,
          vel: n.v,
          hand: n.h,
          t: n.t,
          problem: Boolean(n.problem),
          origIdx: n.origIdx,
        });
      }
    }

    const occupied = Array.from(buckets.keys()).sort((a, b) => a - b);
    if (occupied.length === 0) {
      return [
        {
          isRest: true,
          keys: [clef === "treble" ? "b/4" : "d/3"],
          dur: "wr",
          ticks: 16,
          startSlot: 0,
          items: [],
        },
      ];
    }

    const events = [];
    let cursor = 0;

    for (let idx = 0; idx < occupied.length; idx++) {
      const slot = occupied[idx];
      if (slot > cursor) {
        const restUnits = splitRestGap(slot - cursor, cursor, step);
        restUnits.forEach((ru) =>
          events.push({
            ...ru,
            keys: [clef === "treble" ? "b/4" : "d/3"],
            items: [],
          })
        );
        cursor = slot;
      }

      const nextSlot = idx + 1 < occupied.length ? occupied[idx + 1] : 16;
      const avail = Math.max(step, nextSlot - slot);
      const rawItems = buckets.get(slot).slice().sort((a, b) => a.midi - b.midi);

      // Keep max 4 pitches per chord (preserving lowest, highest, and core inner notes) to keep engraving uncluttered
      let chordItems = rawItems;
      if (rawItems.length > 4) {
        chordItems = [rawItems[0], rawItems[1], rawItems[rawItems.length - 2], rawItems[rawItems.length - 1]];
      }

      const maxDurS = Math.max(...chordItems.map((it) => it.dur));
      const desiredSlots = Math.max(step, Math.round((maxDurS / barDur) * slotsCount) * step);
      const picked = pickNoteDuration(avail, desiredSlots, slot, step);

      // Deduplicate identical VexFlow key strings after enharmonic mapping
      const seenKeys = new Set();
      const dedupItems = [];
      for (const it of chordItems) {
        const k = midiToVexKey(it.midi, keySig);
        if (!seenKeys.has(k)) {
          seenKeys.add(k);
          dedupItems.push({ ...it, vexKey: k });
        }
      }

      events.push({
        isRest: false,
        keys: dedupItems.map((it) => it.vexKey),
        dur: picked.dur,
        ticks: picked.len,
        startSlot: slot,
        items: dedupItems,
      });
      cursor = slot + picked.len;
    }

    if (cursor < 16) {
      const tailRests = splitRestGap(16 - cursor, cursor, step);
      tailRests.forEach((ru) =>
        events.push({
          ...ru,
          keys: [clef === "treble" ? "b/4" : "d/3"],
          items: [],
        })
      );
    }

    return events;
  }

  function normNote(raw, idx, problemSet) {
    if (!raw || typeof raw !== "object") return null;
    const p = Number(raw.pitch ?? raw.note ?? raw.p ?? 0);
    if (!Number.isFinite(p) || p <= 0) return null;
    const t = Number(raw.onset_s ?? raw.time_offset_s ?? raw.t ?? 0);
    const d = Math.max(0.08, Number(raw.duration_s ?? raw.d ?? 0.45));
    const v = Math.max(1, Math.min(127, Number(raw.velocity ?? raw.v ?? 64)));
    const h = String(raw.hand ?? raw.h ?? (p >= 60 ? "rh" : "lh")).toLowerCase();
    return {
      p: Math.max(21, Math.min(108, Math.round(p))),
      t: Number.isFinite(t) ? t : 0,
      d,
      v,
      h,
      origIdx: idx,
      problem: problemSet.has(idx),
    };
  }

  function buildMeasuresPlan(notes, opts) {
    const chords = Array.isArray(opts.chords) ? opts.chords : [];
    const bpm = Math.max(40, Math.min(180, Number(opts.bpm || 74)));
    const barDurDefault = (60 / bpm) * 4;

    let bars = [];
    const timelineBars = chords.filter((c) => c && typeof c === "object" && c.start_s != null && c.end_s != null);
    if (timelineBars.length > 0) {
      bars = timelineBars.map((c, idx) => ({
        barNo: Number(c.bar || idx + 1),
        start_s: Number(c.start_s),
        end_s: Number(c.end_s),
        chord: formatChordSymbol(c.chord || ""),
      }));
    } else {
      const minT = notes.length ? Math.min(...notes.map((n) => n.t)) : 0;
      const rawMaxT = notes.length ? Math.max(...notes.map((n) => n.t + Math.min(1.5, n.d))) : barDurDefault;
      const rawSpan = Math.max(0.5, rawMaxT - minT);
      let estBars = Math.max(1, Math.min(8, Math.round(rawSpan / barDurDefault)));
      if (chords.length >= 2 && chords.length <= 8 && opts.winStart == null) {
        if (rawSpan >= 2.2) estBars = chords.length;
        else if (rawSpan >= 1.1) estBars = Math.min(2, chords.length);
        else estBars = 1;
      } else if (rawSpan < 1.2 && opts.winStart == null) {
        estBars = 1;
      } else if (rawSpan < 2.4 && opts.winStart == null) {
        estBars = Math.min(2, Math.max(1, estBars));
      }
      const span = Math.max(barDurDefault * (estBars === 1 ? 0.6 : 1), rawSpan);
      const eachDur = span / estBars;
      for (let i = 0; i < estBars; i++) {
        const chObj = chords[i];
        const chStr = typeof chObj === "object" && chObj ? chObj.chord : chObj;
        bars.push({
          barNo: i + 1,
          start_s: minT + i * eachDur,
          end_s: minT + (i + 1) * eachDur,
          chord: formatChordSymbol(chStr || ""),
        });
      }
    }

    // Windowing for Mode 4 long pieces or responsive width
    const maxVisibleBars = opts.maxVisibleBars || 4;
    if (opts.winStart != null && opts.winEnd != null && bars.length > maxVisibleBars) {
      const playSec = opts.playheadSec != null ? opts.playheadSec : opts.winStart;
      let anchorIdx = bars.findIndex((b) => playSec >= b.start_s && playSec < b.end_s);
      if (anchorIdx < 0) anchorIdx = 0;
      const startIdx = Math.max(0, Math.min(bars.length - maxVisibleBars, Math.floor(anchorIdx / maxVisibleBars) * maxVisibleBars));
      bars = bars.slice(startIdx, startIdx + maxVisibleBars);
    } else if (bars.length > maxVisibleBars) {
      bars = bars.slice(0, maxVisibleBars);
    }
    return bars;
  }

  function extractCurvePoints(curve, fallbackVal) {
    if (!Array.isArray(curve) || curve.length === 0) {
      return fallbackVal != null ? [{ t: 0, val: Number(fallbackVal) }] : [];
    }
    return curve
      .map((pt) => ({
        t: Number(pt.t ?? pt.onset_s ?? pt.time_offset_s ?? 0),
        val: Number(pt.val ?? pt.value ?? 0),
      }))
      .sort((a, b) => a.t - b.t);
  }

  function velocityToDynamic(avgVel) {
    if (avgVel <= 42) return "pp";
    if (avgVel <= 58) return "p";
    if (avgVel <= 76) return "mp";
    if (avgVel <= 92) return "mf";
    return "f";
  }

  function bboxIntersects(a, b, pad = 2) {
    if (!a || !b) return false;
    return !(
      a.x + a.width + pad <= b.x ||
      b.x + b.width + pad <= a.x ||
      a.y + a.height + pad <= b.y ||
      b.y + b.height + pad <= a.y
    );
  }

  // =========================================================================
  // Main SVG Engraving Entrypoint
  // =========================================================================
  const _svgCache = new WeakMap();

  function clearHost(container) {
    while (container.firstChild) container.removeChild(container.firstChild);
  }

  function renderGrandStaff(container, rawNotes, opts = {}) {
    if (!container) return null;
    if (typeof window.Vex === "undefined" || !window.Vex.Flow) {
      container.textContent = "楽譜レンダリングエンジン読込中...";
      return null;
    }

    const VF = window.Vex.Flow;
    const rect = container.getBoundingClientRect();
    const width = Math.max(240, Math.floor(rect.width || container.clientWidth || 640));
    const compact = Boolean(opts.compact);
    const projClean = Boolean(opts.projectionClean);
    const bgHex = projClean ? "#141517" : "#1C1D20";

    const problemArr = Array.isArray(opts.problemNoteIndices) ? opts.problemNoteIndices.map(Number) : [];
    const problemSet = new Set(problemArr);
    const notes = (rawNotes || [])
      .map((r, i) => normNote(r, i, new Set()))
      .filter(Boolean)
      .sort((a, b) => a.t - b.t || a.p - b.p)
      .map((n, sortedIdx) => ({ ...n, origIdx: sortedIdx, problem: problemSet.has(sortedIdx) }));

    // Responsive bar capacity so measures never squish on narrow viewports
    let maxVisibleBars = 4;
    if (compact) {
      maxVisibleBars = width < 330 ? 1 : 2;
    } else if (width < 460) {
      maxVisibleBars = 1;
    } else if (width < 820) {
      maxVisibleBars = 2;
    } else if (width < 1040) {
      maxVisibleBars = 3;
    }

    const bars = buildMeasuresPlan(notes, { ...opts, maxVisibleBars });
    const keySig = resolveKeySignature(opts.tonality, opts.chords);

    // Build stable cache signature so 60fps Mode 4 playback does not re-engrave unchanged bar windows
    const cacheKey = JSON.stringify({
      w: width,
      compact,
      projClean,
      keySig,
      b0: bars[0]?.barNo,
      bn: bars.length,
      ch0: bars[0]?.chord || "",
      chLast: bars[bars.length - 1]?.chord || "",
      nLen: notes.length,
      nSig: notes.slice(0, 4).map((n) => `${n.p}:${n.t.toFixed(2)}:${n.h}`).join("|"),
      nLast: notes.length ? `${notes[notes.length - 1].p}:${notes[notes.length - 1].t.toFixed(2)}` : "",
      p64: opts.pedalCc64 ?? (Array.isArray(opts.cc64Curve) ? opts.cc64Curve.length : 0),
      p67: opts.pedalCc67 ?? (Array.isArray(opts.cc67Curve) ? opts.cc67Curve.length : 0),
      rej: Boolean(opts.rejected),
      rTxt: opts.rejected ? String(opts.rejectionReason || "") : "",
      prob: problemArr.join(","),
      empty: notes.length === 0 ? String(opts.emptyText || "") : "",
    });

    const cached = _svgCache.get(container);
    if (cached && cached.cacheKey === cacheKey) {
      if (opts.playheadSec != null) {
        updatePlayhead(container, opts.playheadSec);
      }
      return cached.meta;
    }

    clearHost(container);

    const lineSpacing = compact ? 7.5 : 9.5;
    const trebleToBassGap = compact ? 92 : 114;
    const topMargin = compact ? 30 : 38;
    const leftMargin = compact ? 22 : 26;
    const rightMargin = compact ? 10 : 14;
    const usableW = Math.max(160, width - leftMargin - rightMargin);

    // Give Bar 0 extra horizontal room for Clef + KeySignature + TimeSignature + breathing pad
    const keyAccCount = keySig === "C" || keySig === "Am" ? 0 : Math.min(4, Math.max(1, (keySig === "F" || keySig === "Dm" || keySig === "G" || keySig === "Em") ? 1 : (keySig === "Bb" || keySig === "Gm" || keySig === "D" || keySig === "Bm") ? 2 : 3));
    const bar0HeaderExtra = (compact ? 64 : 78) + keyAccCount * 10;
    const totalBarCount = Math.max(1, bars.length);
    const remainW = Math.max(100, usableW - (totalBarCount > 1 ? bar0HeaderExtra : 0));
    const eachNoteW = remainW / totalBarCount;
    const barWidths = bars.map((_, i) => (i === 0 && totalBarCount > 1 ? eachNoteW + bar0HeaderExtra : eachNoteW));

    // Check vertical range across bars to avoid extreme-ledger clipping
    const allPitches = notes.map((n) => n.p);
    const maxPitch = allPitches.length ? Math.max(...allPitches) : 76;
    const minPitch = allPitches.length ? Math.min(...allPitches) : 45;
    const extraTop = maxPitch >= 84 ? Math.min(28, (maxPitch - 81) * 3) : 0;
    const extraMid = (minPitch <= 55 && maxPitch >= 62) ? 6 : 0;
    const extraBot = minPitch <= 36 ? Math.min(24, (38 - minPitch) * 3) : 0;

    const trebleY = topMargin + extraTop;
    const bassY = trebleY + trebleToBassGap + extraMid;
    const bassBotY = bassY + 4 * lineSpacing;
    const initH = Math.max(compact ? 220 : 300, bassBotY + extraBot + (opts.rejected ? 98 : 74));

    const renderer = new VF.Renderer(container, VF.Renderer.Backends.SVG);
    renderer.resize(width, initH);
    const context = renderer.getContext();
    const svg = container.querySelector("svg");
    if (svg) {
      svg.setAttribute("class", "grand-staff-svg");
      svg.setAttribute("width", String(width));
      svg.setAttribute("height", String(initH));
      svg.setAttribute("viewBox", `0 0 ${width} ${initH}`);
      svg.style.display = "block";
      svg.style.width = "100%";
      svg.style.height = "auto";
      svg.style.backgroundColor = bgHex;
    }

    const COLOR_STAFF = "#888C96"; // 5.01:1 on #1C1D20
    const COLOR_LEDGER = "#A8ACB6";
    const COLOR_CLEF = "#EAE6DF";
    const COLOR_RH = "#EAE6DF";
    const COLOR_LH = "#D8D3CA";
    const COLOR_HUMAN = "#A8C8E8";
    const COLOR_VERMILION = "#E85E4B";
    const COLOR_REST = "#9DA1AC";

    const renderedBarsMeta = [];
    const noteElementsWithTime = [];
    const noteheadItemsMeta = []; // For red-pen marking on rejected candidates

    let curX = leftMargin;
    for (let bIdx = 0; bIdx < bars.length; bIdx++) {
      const bar = bars[bIdx];
      const bw = barWidths[bIdx];
      const isFirst = bIdx === 0;
      const isLast = bIdx === bars.length - 1;

      const staveOpts = {
        fill_style: COLOR_STAFF,
        spacing_between_lines_px: lineSpacing,
        space_above_staff_ln: 0,
        space_below_staff_ln: 0,
        left_bar: !isFirst,
        right_bar: true,
      };
      const trebleStave = new VF.Stave(curX, trebleY, bw, staveOpts);
      const bassStave = new VF.Stave(curX, bassY, bw, staveOpts);

      if (isFirst) {
        trebleStave.addClef("treble").addKeySignature(keySig).addTimeSignature("4/4");
        bassStave.addClef("bass").addKeySignature(keySig).addTimeSignature("4/4");
        if (typeof VF.Stave.formatBegModifiers === "function") {
          VF.Stave.formatBegModifiers([trebleStave, bassStave]);
        }
        // Extra right breathing room after clef/key/time so Beat 1 note/accidental never crowds
        const alignedStart = Math.max(trebleStave.getNoteStartX(), bassStave.getNoteStartX()) + (compact ? 8 : 12);
        trebleStave.setNoteStartX(alignedStart);
        bassStave.setNoteStartX(alignedStart);
      } else {
        const alignedStart = Math.max(trebleStave.getNoteStartX(), bassStave.getNoteStartX()) + 6;
        trebleStave.setNoteStartX(alignedStart);
        bassStave.setNoteStartX(alignedStart);
      }
      if (isLast && opts.finalBarline) {
        trebleStave.setEndBarType(VF.Barline.type.NONE);
        bassStave.setEndBarType(VF.Barline.type.NONE);
      }

      context.setFillStyle(COLOR_CLEF);
      context.setStrokeStyle(COLOR_STAFF);
      trebleStave.setContext(context).draw();
      bassStave.setContext(context).draw();

      if (isFirst) {
        context.save();
        context.setFillStyle(COLOR_CLEF);
        context.setStrokeStyle(COLOR_CLEF);
        new VF.StaveConnector(trebleStave, bassStave)
          .setType(VF.StaveConnector.type.BRACE)
          .setContext(context)
          .draw();
        context.restore();

        context.save();
        context.setFillStyle(COLOR_STAFF);
        context.setStrokeStyle(COLOR_STAFF);
        new VF.StaveConnector(trebleStave, bassStave)
          .setType(VF.StaveConnector.type.SINGLE_LEFT)
          .setContext(context)
          .draw();
        context.restore();
      }

      context.save();
      context.setFillStyle(COLOR_STAFF);
      context.setStrokeStyle(COLOR_STAFF);
      new VF.StaveConnector(trebleStave, bassStave)
        .setType(isLast && opts.finalBarline ? VF.StaveConnector.type.BOLD_DOUBLE_RIGHT : VF.StaveConnector.type.SINGLE_RIGHT)
        .setContext(context)
        .draw();
      context.restore();

      // Partition notes inside this bar
      const tol = 0.03;
      const barNotes = notes.filter((n) => n.t >= bar.start_s - tol && n.t < bar.end_s - tol);
      const trebleRaw = barNotes.filter((n) => n.h === "rh" || (n.h !== "lh" && n.p >= 60));
      const bassRaw = barNotes.filter((n) => n.h === "lh" || (n.h === "human" && n.p < 60));

      const noteAreaW = Math.max(40, trebleStave.getNoteEndX() - trebleStave.getNoteStartX() - (compact ? 14 : 24));
      const maxGridStep = noteAreaW >= 165 ? 1 : noteAreaW >= 100 ? 2 : 4;

      const trPlan = quantizeBarVoice(trebleRaw, bar.start_s, bar.end_s, "treble", keySig, maxGridStep);
      const bsPlan = quantizeBarVoice(bassRaw, bar.start_s, bar.end_s, "bass", keySig, maxGridStep);

      const makeStaveNotes = (plan, clefName) => {
        const list = [];
        for (const ev of plan) {
          const sn = new VF.StaveNote({
            clef: clefName,
            keys: ev.keys,
            duration: ev.dur,
            auto_stem: !ev.isRest,
          });
          sn.setLedgerLineStyle({ strokeStyle: COLOR_LEDGER, lineWidth: 1.3 });

          if (ev.isRest) {
            sn.setStyle({ fillStyle: COLOR_REST, strokeStyle: COLOR_REST });
          } else {
            if (ev.dur.endsWith("d") && VF.Dot && typeof VF.Dot.buildAndAttach === "function") {
              VF.Dot.buildAndAttach([sn], { all: true });
            }
            const hasNonHuman = ev.items.some((it) => it.hand !== "human");
            const allHuman = ev.items.every((it) => it.hand === "human");
            const hasProb = Boolean(opts.rejected) && ev.items.some((it) => it.problem);
            const stemColor = hasProb
              ? COLOR_VERMILION
              : allHuman
              ? COLOR_HUMAN
              : clefName === "bass" && !hasNonHuman
              ? COLOR_HUMAN
              : clefName === "bass"
              ? COLOR_LH
              : COLOR_RH;
            sn.setStyle({ fillStyle: stemColor, strokeStyle: stemColor });
            ev.items.forEach((it, kIdx) => {
              const kc = opts.rejected && it.problem ? COLOR_VERMILION : it.hand === "human" ? COLOR_HUMAN : clefName === "bass" ? COLOR_LH : COLOR_RH;
              sn.setKeyStyle(kIdx, { fillStyle: kc, strokeStyle: kc });
            });
          }
          sn.__diskMeta = ev;
          list.push(sn);
        }
        return list;
      };

      const trNotes = makeStaveNotes(trPlan, "treble");
      const bsNotes = makeStaveNotes(bsPlan, "bass");

      const trVoice = new VF.Voice({ num_beats: 4, beat_value: 4 }).setStrict(false).addTickables(trNotes);
      const bsVoice = new VF.Voice({ num_beats: 4, beat_value: 4 }).setStrict(false).addTickables(bsNotes);

      // Independent Treble & Bass bar-level accidental management (Rule 4)
      VF.Accidental.applyAccidentals([trVoice], keySig);
      VF.Accidental.applyAccidentals([bsVoice], keySig);

      // Format voices together across the exact note area
      new VF.Formatter().joinVoices([trVoice]).joinVoices([bsVoice]).format([trVoice, bsVoice], noteAreaW);

      // Beat-grouped beaming (Rule 3) — color beams cleanly
      const trBeams = VF.Beam.generateBeams(trNotes, { groups: [new VF.Fraction(2, 8)], beam_rests: false });
      const bsBeams = VF.Beam.generateBeams(bsNotes, { groups: [new VF.Fraction(2, 8)], beam_rests: false });

      trBeams.forEach((bm) => {
        const firstNote = bm.getNotes()[0];
        const col = (firstNote && firstNote.getStyle ? firstNote.getStyle()?.fillStyle : null) || firstNote?.style?.fillStyle || COLOR_RH;
        bm.setStyle({ fillStyle: col, strokeStyle: col });
      });
      bsBeams.forEach((bm) => {
        const firstNote = bm.getNotes()[0];
        const col = (firstNote && firstNote.getStyle ? firstNote.getStyle()?.fillStyle : null) || firstNote?.style?.fillStyle || COLOR_LH;
        bm.setStyle({ fillStyle: col, strokeStyle: col });
      });

      trVoice.draw(context, trebleStave);
      bsVoice.draw(context, bassStave);
      trBeams.forEach((bm) => bm.setContext(context).draw());
      bsBeams.forEach((bm) => bm.setContext(context).draw());

      // Collect timing & problem-note coordinates for playhead & red-pen marker
      const collectNoteDOM = (vfList) => {
        vfList.forEach((sn) => {
          const meta = sn.__diskMeta;
          const el = sn.getSVGElement ? sn.getSVGElement() : null;
          if (!el || !meta || meta.isRest) return;
          const minT = Math.min(...meta.items.map((it) => it.t));
          const maxEnd = Math.max(...meta.items.map((it) => it.t + it.dur));
          noteElementsWithTime.push({ el, start_s: minT, end_s: maxEnd });
          if (opts.rejected && meta.items.some((it) => it.problem)) {
            noteheadItemsMeta.push(el);
          }
        });
      };
      collectNoteDOM(trNotes);
      collectNoteDOM(bsNotes);

      const avgVel = barNotes.length ? barNotes.reduce((a, b) => a + b.v, 0) / barNotes.length : 64;
      renderedBarsMeta.push({
        barNo: bar.barNo,
        chord: bar.chord,
        start_s: bar.start_s,
        end_s: bar.end_s,
        barX: curX,
        barWidth: bw,
        noteStartX: trebleStave.getNoteStartX(),
        noteEndX: trebleStave.getNoteEndX(),
        dynamic: velocityToDynamic(avgVel),
        hasNotes: barNotes.length > 0,
      });

      curX += bw;
    }

    // Post-color safeguard: replace any default VexFlow black (#000 / black) SVG shapes with crisp high-contrast colors
    if (svg) {
      svg.querySelectorAll("path, rect, line, circle, polygon").forEach((el) => {
        const fill = (el.getAttribute("fill") || "").toLowerCase();
        const stroke = (el.getAttribute("stroke") || "").toLowerCase();
        const inStave = Boolean(el.closest(".vf-stave, .vf-stavebarline"));
        const target = inStave ? COLOR_STAFF : COLOR_CLEF;
        if (fill === "#000" || fill === "#000000" || fill === "black") el.setAttribute("fill", target);
        if (stroke === "#000" || stroke === "#000000" || stroke === "black") el.setAttribute("stroke", target);
      });
    }

    // =======================================================================
    // Dynamic Bounding-Box Lane Placement (Zero SVG Overlap Guarantee)
    // =======================================================================
    const SVG_NS = "http://www.w3.org/2000/svg";
    let minGraphicY = trebleY;
    let maxGraphicY = bassBotY;
    const obstacleBoxes = [];

    if (svg) {
      svg.querySelectorAll(".vf-stavenote, .vf-clef, .vf-keysig, .vf-timesig, .vf-accidental, .vf-beam").forEach((g) => {
        try {
          const b = g.getBBox();
          if (b && b.width > 0 && b.height > 0) {
            minGraphicY = Math.min(minGraphicY, b.y);
            maxGraphicY = Math.max(maxGraphicY, b.y + b.height);
            obstacleBoxes.push(b);
          }
        } catch (_) {}
      });
    }

    // Lane 1: Chords & Measure Numbers strictly ABOVE all Treble notes/ledger lines
    const lane1Y = Math.min(trebleY - 18, minGraphicY - 12);
    const shiftDownY = lane1Y < 22 ? Math.ceil(22 - lane1Y) : 0;

    let mainLayer = svg;
    if (svg && shiftDownY > 0) {
      // Shift VexFlow notation group down cleanly so top lane never clips at y < 0
      const gWrap = document.createElementNS(SVG_NS, "g");
      gWrap.setAttribute("transform", `translate(0, ${shiftDownY})`);
      while (svg.firstChild) gWrap.appendChild(svg.firstChild);
      svg.appendChild(gWrap);
      mainLayer = svg;
    }

    const chordLaneY = lane1Y + shiftDownY;
    const adjTrebleBot = trebleY + 4 * lineSpacing + shiftDownY;
    const adjBassTop = bassY + shiftDownY;
    const adjBassBot = bassBotY + shiftDownY;
    const adjMaxGraphicY = maxGraphicY + shiftDownY;

    const addSvgText = (txt, x, y, attrs = {}) => {
      if (!svg) return null;
      const el = document.createElementNS(SVG_NS, "text");
      el.setAttribute("x", String(Math.round(x * 10) / 10));
      el.setAttribute("y", String(Math.round(y * 10) / 10));
      el.setAttribute("font-size", attrs.fontSize || "13.5");
      el.setAttribute("font-family", attrs.fontFamily || '"JetBrains Mono", "Cascadia Mono", monospace');
      el.setAttribute("font-weight", attrs.fontWeight || "600");
      el.setAttribute("fill", attrs.fill || COLOR_CLEF);
      if (attrs.fontStyle) el.setAttribute("font-style", attrs.fontStyle);
      if (attrs.textAnchor) el.setAttribute("text-anchor", attrs.textAnchor);
      el.textContent = txt;
      svg.appendChild(el);
      return el;
    };

    const addSvgLine = (x1, y1, x2, y2, attrs = {}) => {
      if (!svg) return null;
      const el = document.createElementNS(SVG_NS, "line");
      el.setAttribute("x1", String(x1));
      el.setAttribute("y1", String(y1));
      el.setAttribute("x2", String(x2));
      el.setAttribute("y2", String(y2));
      el.setAttribute("stroke", attrs.stroke || COLOR_CLEF);
      el.setAttribute("stroke-width", String(attrs.strokeWidth || 1.5));
      if (attrs.strokeDasharray) el.setAttribute("stroke-dasharray", attrs.strokeDasharray);
      svg.appendChild(el);
      return el;
    };

    const addSvgPath = (d, attrs = {}) => {
      if (!svg) return null;
      const el = document.createElementNS(SVG_NS, "path");
      el.setAttribute("d", d);
      el.setAttribute("fill", attrs.fill || "none");
      el.setAttribute("stroke", attrs.stroke || COLOR_CLEF);
      el.setAttribute("stroke-width", String(attrs.strokeWidth || 1.5));
      svg.appendChild(el);
      return el;
    };

    if (svg) {
      // Render Lane 1: Bar number + Chord symbol (non-overlapping horizontally)
      let prevRightEdge = 0;
      renderedBarsMeta.forEach((bm, idx) => {
        const mTxt = `${bm.barNo}`;
        const label = bm.chord ? `${mTxt}. ${bm.chord}` : `第${mTxt}小節`;
        const lx = Math.max(prevRightEdge + 10, idx === 0 ? bm.noteStartX - 6 : bm.barX + 6);
        if (lx < width - 38) {
          const tel = addSvgText(label, lx, chordLaneY, {
            fontSize: compact ? "13" : "14",
            fill: COLOR_CLEF,
            fontFamily: '"Cormorant Garamond", "JetBrains Mono", serif',
            fontWeight: "700",
          });
          const tb = tel.getBBox();
          if (tb.x + tb.width > width - rightMargin) {
            tel.textContent = bm.chord || mTxt;
          }
          const tb2 = tel.getBBox();
          prevRightEdge = tb2.x + tb2.width;
        }
      });

      // Render Lane 2: Dynamics (pp/mp/mf/f) inside inter-stave gap only where clear of note obstacles
      if (!compact && renderedBarsMeta.length > 0) {
        const dynY = Math.round((adjTrebleBot + adjBassTop) * 0.5) + 4;
        const shiftedObstacles = obstacleBoxes.map((b) => ({
          x: b.x,
          y: b.y + shiftDownY,
          width: b.width,
          height: b.height,
        }));
        let lastDyn = "";
        let prevDynRight = 0;
        renderedBarsMeta.forEach((bm) => {
          if (!bm.hasNotes || bm.dynamic === lastDyn) return;
          const dx = Math.max(prevDynRight + 16, bm.noteStartX + 4);
          if (dx > width - 32) return;
          const del = addSvgText(bm.dynamic, dx, dynY, {
            fontSize: "14.5",
            fontFamily: '"Cormorant Garamond", Georgia, serif',
            fontStyle: "italic",
            fontWeight: "700",
            fill: "#D8D3CA",
          });
          const db = del.getBBox();
          const collides = shiftedObstacles.some((ob) => bboxIntersects(db, ob, 4));
          if (collides) {
            // Try sliding slightly right inside same bar, else remove to guarantee 0px overlap
            const altX = Math.min(bm.noteEndX - db.width - 4, bm.noteStartX + (bm.noteEndX - bm.noteStartX) * 0.45);
            del.setAttribute("x", String(Math.round(altX)));
            const db2 = del.getBBox();
            if (altX <= prevDynRight + 10 || shiftedObstacles.some((ob) => bboxIntersects(db2, ob, 4))) {
              del.remove();
              return;
            }
          }
          const fdb = del.getBBox();
          prevDynRight = fdb.x + fdb.width;
          lastDyn = bm.dynamic;
        });
      }

      // Render Lane 3: Dedicated Left/Right Pedal Lanes strictly BELOW Bass & low ledger lines
      const pedalLaneTop = Math.max(adjBassBot + 22, adjMaxGraphicY + 14);
      let cursorBottomY = pedalLaneTop;

      const cc67Pts = extractCurvePoints(opts.cc67Curve, opts.pedalCc67);
      const cc64Pts = extractCurvePoints(opts.cc64Curve, opts.pedalCc64);
      const barFirstStart = renderedBarsMeta[0]?.start_s ?? 0;
      const barLastEnd = renderedBarsMeta[renderedBarsMeta.length - 1]?.end_s ?? 4;
      const barFirstNoteX = renderedBarsMeta[0]?.noteStartX ?? leftMargin + 70;
      const barLastEndX = renderedBarsMeta[renderedBarsMeta.length - 1]?.noteEndX ?? width - rightMargin;

      if (!compact && (cc67Pts.length > 0 || cc64Pts.length > 0)) {
        const unaY = pedalLaneTop + 14;
        const pedY = unaY + 22;

        // Left-aligned labels -> measure exact width so horizontal brackets never collide
        const lblUna = addSvgText("Una Corda", leftMargin, unaY, {
          fontSize: "13",
          fontFamily: '"Cormorant Garamond", serif',
          fontStyle: "italic",
          fontWeight: "600",
          fill: COLOR_VERMILION,
        });
        const lblPed = addSvgText("Ped.", leftMargin, pedY, {
          fontSize: "13.5",
          fontFamily: '"Cormorant Garamond", serif',
          fontStyle: "italic",
          fontWeight: "700",
          fill: COLOR_CLEF,
        });
        const labelRight = Math.max(
          lblUna.getBBox().x + lblUna.getBBox().width,
          lblPed.getBBox().x + lblPed.getBBox().width
        );
        const lineStartX = Math.max(barFirstNoteX, labelRight + 12);
        const lineEndX = Math.max(lineStartX + 36, barLastEndX - 4);
        const totalSpan = Math.max(0.25, barLastEnd - barFirstStart);
        const tToX = (t) => lineStartX + Math.max(0, Math.min(1, (t - barFirstStart) / totalSpan)) * (lineEndX - lineStartX);

        // Una Corda (CC#67) bracket where value >= 25
        let unaPath = "";
        let unaActive = false;
        const val67At = (t) => {
          let v = cc67Pts[0]?.val ?? 0;
          for (const p of cc67Pts) {
            if (p.t <= t + 0.01) v = p.val;
            else break;
          }
          return v;
        };
        const samples = Math.max(12, renderedBarsMeta.length * 6);
        for (let s = 0; s <= samples; s++) {
          const ts = barFirstStart + (s / samples) * totalSpan;
          const x = tToX(ts);
          const v = val67At(ts);
          if (v >= 25) {
            if (!unaActive) {
              unaPath += `M ${x.toFixed(1)} ${(unaY - 8).toFixed(1)} L ${x.toFixed(1)} ${(unaY - 3).toFixed(1)} `;
              unaActive = true;
            } else {
              unaPath += `L ${x.toFixed(1)} ${(unaY - 3).toFixed(1)} `;
            }
          } else if (unaActive) {
            unaPath += `L ${x.toFixed(1)} ${(unaY - 8).toFixed(1)} `;
            unaActive = false;
          }
        }
        if (unaActive) {
          unaPath += `L ${lineEndX.toFixed(1)} ${(unaY - 8).toFixed(1)}`;
        }
        if (unaPath) {
          addSvgPath(unaPath, { stroke: COLOR_VERMILION, strokeWidth: 1.5 });
        } else {
          addSvgLine(lineStartX, unaY - 3, lineEndX, unaY - 3, {
            stroke: "#484B53",
            strokeWidth: 1,
            strokeDasharray: "3 4",
          });
        }

        // Damper Ped. (CC#64) continuous line with repedal notches at chord boundaries
        let pedD = `M ${lineStartX.toFixed(1)} ${(pedY - 3).toFixed(1)} `;
        const val64At = (t) => {
          let v = cc64Pts[0]?.val ?? 0;
          for (const p of cc64Pts) {
            if (p.t <= t + 0.01) v = p.val;
            else break;
          }
          return v;
        };
        let hasPedal = cc64Pts.some((p) => p.val >= 25);
        if (hasPedal) {
          for (let i = 1; i < renderedBarsMeta.length; i++) {
            const bx = renderedBarsMeta[i].barX;
            if (bx > lineStartX + 10 && bx < lineEndX - 10) {
              pedD += `L ${(bx - 5).toFixed(1)} ${(pedY - 3).toFixed(1)} L ${bx.toFixed(1)} ${(pedY - 10).toFixed(1)} L ${(bx + 5).toFixed(1)} ${(pedY - 3).toFixed(1)} `;
            }
          }
          pedD += `L ${lineEndX.toFixed(1)} ${(pedY - 3).toFixed(1)}`;
          addSvgPath(pedD, { stroke: COLOR_CLEF, strokeWidth: 1.5 });
        } else {
          addSvgLine(lineStartX, pedY - 3, lineEndX, pedY - 3, {
            stroke: "#484B53",
            strokeWidth: 1,
            strokeDasharray: "3 4",
          });
        }
        cursorBottomY = pedY + 12;
      }

      // Render Lane 4 (Rule 8): Rejected candidate clean non-obscuring red-pen ellipse + bottom reason lane
      if (opts.rejected) {
        let targets = noteheadItemsMeta;
        if (targets.length === 0 && noteElementsWithTime.length > 0) {
          targets = [noteElementsWithTime[0].el];
        }
        targets.slice(0, 3).forEach((g) => {
          const nh = g.querySelector(".vf-notehead") || g;
          try {
            const nb = nh.getBBox();
            if (nb && nb.width > 0) {
              const ell = document.createElementNS(SVG_NS, "ellipse");
              ell.setAttribute("cx", String((nb.x + nb.width * 0.5).toFixed(1)));
              ell.setAttribute("cy", String((nb.y + nb.height * 0.5 + shiftDownY).toFixed(1)));
              ell.setAttribute("rx", String(Math.max(8, nb.width * 0.85).toFixed(1)));
              ell.setAttribute("ry", String(Math.max(6, nb.height * 0.85).toFixed(1)));
              ell.setAttribute("fill", "none");
              ell.setAttribute("stroke", COLOR_VERMILION);
              ell.setAttribute("stroke-width", "1.8");
              svg.appendChild(ell);
            }
          } catch (_) {}
        });

        const reasonTxt = formatMusicText(String(opts.rejectionReason || "和声・声部監査により棄却"));
        const rejY = cursorBottomY + 14;
        const maxTextW = Math.max(120, width - leftMargin - rightMargin);
        const measEl = addSvgText(reasonTxt, leftMargin, rejY, {
          fontSize: "13",
          fontFamily: '"Noto Sans JP", sans-serif',
          fontWeight: "600",
          fill: COLOR_VERMILION,
        });
        if (measEl.getBBox().width <= maxTextW) {
          cursorBottomY = rejY + 12;
        } else {
          measEl.remove();
          const lines = [];
          let curLine = "";
          for (const ch of reasonTxt) {
            const testStr = curLine + ch;
            const probe = addSvgText(testStr, leftMargin, rejY, {
              fontSize: "13",
              fontFamily: '"Noto Sans JP", sans-serif',
              fontWeight: "600",
              fill: COLOR_VERMILION,
            });
            const fits = probe.getBBox().width <= maxTextW;
            probe.remove();
            if (fits || !curLine) {
              curLine = testStr;
            } else {
              lines.push(curLine);
              curLine = ch;
            }
          }
          if (curLine) lines.push(curLine);
          let lineY = rejY;
          lines.forEach((ln) => {
            const lEl = addSvgText(ln, leftMargin, lineY, {
              fontSize: "13",
              fontFamily: '"Noto Sans JP", sans-serif',
              fontWeight: "600",
              fill: COLOR_VERMILION,
            });
            const lb = lEl.getBBox();
            cursorBottomY = lb.y + lb.height + 10;
            lineY += 18;
          });
        }
      }

      if (notes.length === 0 && opts.emptyText && !compact) {
        const emptyY = Math.round((adjTrebleBot + adjBassTop) * 0.5) + 4;
        const emptyX = Math.min(width - rightMargin - 20, Math.max(leftMargin + 80, barFirstNoteX + 10));
        const eel = addSvgText(String(opts.emptyText), emptyX, emptyY, {
          fontSize: "13.5",
          fontFamily: '"Noto Sans JP", sans-serif',
          fontWeight: "500",
          fill: "#A49F96",
        });
        if (eel.getBBox().x + eel.getBBox().width > width - rightMargin) {
          eel.remove();
        }
      }

      // Playhead line (updated smoothly without re-engraving)
      const ph = document.createElementNS(SVG_NS, "line");
      ph.setAttribute("class", "score-playhead");
      ph.setAttribute("x1", String(barFirstNoteX));
      ph.setAttribute("x2", String(barFirstNoteX));
      ph.setAttribute("y1", String(trebleY + shiftDownY - 12));
      ph.setAttribute("y2", String(adjBassBot + 12));
      ph.setAttribute("stroke", COLOR_VERMILION);
      ph.setAttribute("stroke-width", "1.8");
      ph.setAttribute("visibility", "hidden");
      svg.appendChild(ph);

      // Final tight SVG height / viewBox fit so nothing ever clips vertically
      const finalH = Math.ceil(Math.max(cursorBottomY + 8, adjBassBot + 24));
      svg.setAttribute("height", String(finalH));
      svg.setAttribute("viewBox", `0 0 ${width} ${finalH}`);
      container.style.minHeight = `${finalH}px`;
    }

    const meta = {
      width,
      bars: renderedBarsMeta,
      noteElementsWithTime,
      firstStartS: renderedBarsMeta[0]?.start_s ?? 0,
      lastEndS: renderedBarsMeta[renderedBarsMeta.length - 1]?.end_s ?? 4,
      firstNoteX: renderedBarsMeta[0]?.noteStartX ?? leftMargin + 70,
      lastEndX: renderedBarsMeta[renderedBarsMeta.length - 1]?.noteEndX ?? width - rightMargin,
    };

    _svgCache.set(container, { cacheKey, meta });

    if (opts.playheadSec != null) {
      updatePlayhead(container, opts.playheadSec);
    }
    return meta;
  }

  function updatePlayhead(container, playheadSec) {
    const cached = _svgCache.get(container);
    if (!cached || !cached.meta) return;
    const svg = container.querySelector("svg");
    if (!svg) return;
    const ph = svg.querySelector(".score-playhead");
    const m = cached.meta;

    // Interpolate playhead X inside the exact active measure
    let px = null;
    for (const bm of m.bars) {
      if (playheadSec >= bm.start_s && playheadSec <= bm.end_s) {
        const span = Math.max(0.1, bm.end_s - bm.start_s);
        const ratio = (playheadSec - bm.start_s) / span;
        px = bm.noteStartX + ratio * Math.max(12, bm.noteEndX - bm.noteStartX);
        break;
      }
    }
    if (ph) {
      if (px == null) {
        ph.setAttribute("visibility", "hidden");
      } else {
        ph.setAttribute("x1", String(px.toFixed(1)));
        ph.setAttribute("x2", String(px.toFixed(1)));
        ph.setAttribute("visibility", "visible");
      }
    }

    m.noteElementsWithTime.forEach((item) => {
      const playing = playheadSec >= item.start_s - 0.03 && playheadSec <= item.end_s;
      item.el.classList.toggle("note-playing", playing);
      if (playing) {
        item.el.querySelectorAll("path, rect, circle, ellipse").forEach((ch) => {
          if (!ch.dataset.origFill) ch.dataset.origFill = ch.getAttribute("fill") || "#EAE6DF";
          if (!ch.dataset.origStroke) ch.dataset.origStroke = ch.getAttribute("stroke") || "#EAE6DF";
          ch.setAttribute("fill", "#E85E4B");
          ch.setAttribute("stroke", "#E85E4B");
        });
      } else if (item.el.querySelector("[data-orig-fill]")) {
        item.el.querySelectorAll("path, rect, circle, ellipse").forEach((ch) => {
          if (ch.dataset.origFill) ch.setAttribute("fill", ch.dataset.origFill);
          if (ch.dataset.origStroke) ch.setAttribute("stroke", ch.dataset.origStroke);
        });
      }
    });
  }

  function xToTime(container, clientX) {
    const cached = _svgCache.get(container);
    const rect = container.getBoundingClientRect();
    if (!cached || !cached.meta || !rect.width) return null;
    const m = cached.meta;
    const svgX = ((clientX - rect.left) / rect.width) * m.width;
    for (const bm of m.bars) {
      if (svgX >= bm.barX && svgX <= bm.barX + bm.barWidth) {
        const ratio = Math.max(0, Math.min(1, (svgX - bm.noteStartX) / Math.max(12, bm.noteEndX - bm.noteStartX)));
        return bm.start_s + ratio * (bm.end_s - bm.start_s);
      }
    }
    const overallRatio = Math.max(0, Math.min(1, (svgX - m.firstNoteX) / Math.max(20, m.lastEndX - m.firstNoteX)));
    return m.firstStartS + overallRatio * (m.lastEndS - m.firstStartS);
  }

  window.DisklavierScoreEngraver = {
    renderGrandStaff,
    updatePlayhead,
    xToTime,
    formatChordSymbol,
    formatNoteName,
    formatMusicText,
    formatTonalityJa,
    formatPhraseFunctionJa,
    formatEngineJa,
    formatArticulationJa,
    formatTurnReasonJa,
    formatStanceJa,
    formatPedalStrategyJa,
    formatTextureJa,
    formatVoicingJa,
    formatCompactContextJa,
  };
})();
