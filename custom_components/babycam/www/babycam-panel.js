/* BabyCam sidebar panel - plain web component, no external dependencies (fully local). */

const STYLE = `
  :host { display: block; height: 100%; overflow: auto; background: var(--primary-background-color); color: var(--primary-text-color); font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
  .toolbar { display: flex; align-items: center; gap: 12px; height: 56px; padding: 0 16px; background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, #fff); font-size: 20px; }
  .toolbar select { margin-left: auto; }
  main { padding: 16px; display: grid; gap: 16px; grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr); max-width: 1400px; margin: 0 auto; }
  @media (max-width: 900px) { main { grid-template-columns: 1fr; } }
  .card { background: var(--card-background-color, #fff); border-radius: var(--ha-card-border-radius, 12px); box-shadow: var(--ha-card-box-shadow, 0 2px 4px rgba(0,0,0,.2)); padding: 16px; }
  .card h2 { margin: 0 0 12px; font-size: 16px; font-weight: 500; }
  .view { position: relative; background: #000; border-radius: 8px; overflow: hidden; aspect-ratio: 16/9; }
  .view img { width: 100%; height: 100%; object-fit: contain; display: block; }
  .overlay { position: absolute; border: 2px dashed rgba(255,255,255,.6); pointer-events: none; }
  .det { position: absolute; border: 2px solid var(--success-color, #4caf50); pointer-events: none; }
  .det span { position: absolute; top: -18px; left: 0; background: var(--success-color, #4caf50); color: #fff; font-size: 11px; padding: 1px 4px; border-radius: 3px; }
  .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 12px; }
  button { border: none; border-radius: 6px; padding: 10px 16px; font-size: 14px; cursor: pointer; background: var(--secondary-background-color); color: var(--primary-text-color); }
  button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  button.danger { background: var(--error-color, #db4437); color: #fff; }
  button:disabled { opacity: .5; cursor: default; }
  button[hidden] { display: none; }
  select { padding: 8px; border-radius: 6px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
  .status { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .pill { display: flex; align-items: center; gap: 8px; padding: 10px; border-radius: 8px; background: var(--secondary-background-color); }
  .dot { width: 12px; height: 12px; border-radius: 50%; background: var(--disabled-text-color, #9e9e9e); flex: none; }
  .dot.on { background: var(--success-color, #4caf50); }
  .dot.alarm { background: var(--error-color, #db4437); animation: blink 1s infinite; }
  .dot.warn { background: var(--warning-color, #ff9800); }
  @keyframes blink { 50% { opacity: .3; } }
  .banner { padding: 12px 16px; border-radius: 8px; margin-bottom: 12px; font-weight: 500; }
  .banner.alarm { background: var(--error-color, #db4437); color: #fff; }
  .banner.warn { background: var(--warning-color, #ff9800); color: #000; }
  .banner:empty { display: none; }
  .meta { font-size: 12px; color: var(--secondary-text-color); margin-top: 8px; }
  label.toggle { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--divider-color); }
  label.toggle:last-of-type { border-bottom: none; }
  audio { width: 100%; margin-top: 8px; }
  audio[hidden] { display: none; }
  .empty { padding: 32px; text-align: center; color: var(--secondary-text-color); }
`;

class BabyCamPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._entries = [];
    this._entryId = null;
    this._data = null;
    this._unsub = null;
    this._snapshotTimer = null;
    this._audioOn = false;
    this._audioNonce = 0;
    this._dlError = null;
    this._hass = null;
    this._rendered = false;
    this._shellFor = null;
    this._viewMode = null;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._init();
    else this._updateEntityDriven();
  }

  get hass() {
    return this._hass;
  }

  disconnectedCallback() {
    this._stopSubscription();
    this._stopSnapshotLoop();
  }

  async _init() {
    this._render();
    try {
      this._entries = await this._hass.callWS({ type: "babycam/list" });
    } catch (err) {
      this._entries = [];
      console.error("babycam/list failed", err);
    }
    if (this._entries.length) {
      const saved = localStorage.getItem("babycam-entry");
      this._entryId = this._entries.some((e) => e.entry_id === saved) ? saved : this._entries[0].entry_id;
      await this._subscribe();
    }
    this._render();
  }

  async _subscribe() {
    this._stopSubscription();
    this._unsub = await this._hass.connection.subscribeMessage(
      (payload) => {
        this._data = payload;
        this._render();
      },
      { type: "babycam/subscribe", entry_id: this._entryId }
    );
  }

  _stopSubscription() {
    if (this._unsub) {
      this._unsub();
      this._unsub = null;
    }
  }

  _call(type, extra = {}) {
    return this._hass.callWS({ type: `babycam/${type}`, entry_id: this._entryId, ...extra });
  }

  _cameraUrl(stream) {
    const camId = this._data?.entities?.live;
    const st = camId && this._hass.states[camId];
    if (!st) return null;
    const token = st.attributes.access_token;
    const base = stream ? "/api/camera_proxy_stream/" : "/api/camera_proxy/";
    return `${base}${camId}?token=${token}${stream ? "" : `&t=${Date.now()}`}`;
  }

  _startSnapshotLoop(intervalMs) {
    this._stopSnapshotLoop();
    this._snapshotTimer = setInterval(() => {
      const img = this.shadowRoot.querySelector("img.snap");
      const url = this._cameraUrl(false);
      if (img && url) img.src = url;
    }, intervalMs);
  }

  _stopSnapshotLoop() {
    if (this._snapshotTimer) {
      clearInterval(this._snapshotTimer);
      this._snapshotTimer = null;
    }
  }

  _updateEntityDriven() {
    // Toggle switches reflect entity state; refresh them cheaply without a full re-render.
    const ents = this._data?.entities || {};
    this.shadowRoot.querySelectorAll("input[data-key]").forEach((el) => {
      const st = ents[el.dataset.key] && this._hass.states[ents[el.dataset.key]];
      if (st) el.checked = st.state === "on";
    });
  }

  _fmt(ts) {
    if (!ts) return "–";
    return new Date(ts).toLocaleTimeString();
  }

  _render() {
    const root = this.shadowRoot;
    if (!this._rendered) {
      root.innerHTML = `<style>${STYLE}</style><div class="toolbar"><span>BabyCam</span><select id="entry"></select></div><main id="main"></main>`;
      root.getElementById("entry").addEventListener("change", async (ev) => {
        this._entryId = ev.target.value;
        localStorage.setItem("babycam-entry", this._entryId);
        this._data = null;
        this._shellFor = null;
        await this._subscribe();
      });
      this._rendered = true;
    }

    const sel = root.getElementById("entry");
    sel.style.display = this._entries.length > 1 ? "" : "none";
    sel.innerHTML = this._entries
      .map((e) => `<option value="${e.entry_id}" ${e.entry_id === this._entryId ? "selected" : ""}>${e.title}</option>`)
      .join("");

    const main = root.getElementById("main");
    if (!this._entries.length) {
      this._shellFor = null;
      main.innerHTML = `<div class="card empty" style="grid-column: 1 / -1">No BabyCam configured yet. Add one via Settings → Devices &amp; services → Add integration → BabyCam.</div>`;
      return;
    }
    if (!this._data) {
      this._shellFor = null;
      main.innerHTML = `<div class="card empty" style="grid-column: 1 / -1">Loading…</div>`;
      return;
    }
    if (this._shellFor !== this._entryId) this._renderShell(main);
    this._update(main);
  }

  /* Static skeleton; built once per entry so the live stream and audio element survive updates. */
  _renderShell(main) {
    main.innerHTML = `
      <section>
        <div id="banner" class="banner"></div>
        <div class="card">
          <div class="view"><div id="viewInner"></div><div id="roi" class="overlay" hidden></div><div id="dets"></div></div>
          <div class="row">
            <select id="mode">
              <option value="snapshots">Snapshots</option>
              <option value="low">Live – low definition</option>
              <option value="high">Live – high definition</option>
            </select>
            <button id="check">Check now</button>
            <button id="start" class="primary">Start monitoring manually</button>
            <button id="stop" class="danger">Stop monitoring</button>
            <button id="audio">Listen</button>
          </div>
          <audio id="player" controls hidden></audio>
          <div id="meta" class="meta"></div>
        </div>
      </section>
      <aside>
        <div class="card">
          <h2>Status</h2>
          <div class="status">
            <div class="pill"><span id="dot-baby" class="dot"></span><span id="txt-baby"></span></div>
            <div class="pill"><span id="dot-mon" class="dot"></span><span id="txt-mon"></span></div>
            <div class="pill"><span id="dot-cry" class="dot"></span><span id="txt-cry"></span></div>
            <div class="pill"><span id="dot-cam" class="dot"></span><span id="txt-cam"></span></div>
          </div>
          <div id="meta2" class="meta"></div>
        </div>
        <div class="card" style="margin-top:16px">
          <h2>Settings</h2>
          <label class="toggle"><span>AI presence detection</span><input type="checkbox" data-key="ai_detection"></label>
          <label class="toggle"><span>Cry detection</span><input type="checkbox" data-key="cry_detection"></label>
          <label class="toggle"><span>Notifications</span><input type="checkbox" data-key="notifications"></label>
          <div class="meta">Snapshot interval, thresholds, notification targets and media players are configured via the integration options and the number entities.</div>
        </div>
        <div class="card" style="margin-top:16px">
          <h2>AI models</h2>
          <div id="models" class="meta" style="margin-top:0"></div>
          <div class="row"><button id="dl">Download / update models</button></div>
        </div>
      </aside>`;

    main.querySelector("#mode").addEventListener("change", (ev) => this._call("set_stream_mode", { mode: ev.target.value }));
    main.querySelector("#check").addEventListener("click", () => this._call("check_now"));
    main.querySelector("#start").addEventListener("click", () => this._call("start_monitoring"));
    main.querySelector("#stop").addEventListener("click", () => this._call("stop_monitoring"));
    main.querySelector("#audio").addEventListener("click", () => this._toggleAudio());
    main.querySelector("#dl").addEventListener("click", async () => {
      this._dlError = null;
      try {
        await this._call("download_models");
      } catch (err) {
        this._dlError = err?.message || String(err);
        this._render();
      }
    });
    main.querySelectorAll("input[data-key]").forEach((el) =>
      el.addEventListener("change", (ev) => {
        const id = ev.target.dataset.entity;
        if (id) this._hass.callService("switch", ev.target.checked ? "turn_on" : "turn_off", { entity_id: id });
      })
    );
    this._shellFor = this._entryId;
    this._viewMode = null;
  }

  _update(main) {
    const d = this._data;
    const s = d.state;
    const mode = s.stream_mode;
    const monitoring = s.monitoring !== "off";
    const ents = d.entities || {};
    const q = (id) => main.querySelector(`#${id}`);

    // Live view: only touch the <img> when the mode changes (an MJPEG stream must not be restarted on every update).
    if (this._viewMode !== mode) {
      const url = this._cameraUrl(mode !== "snapshots");
      q("viewInner").innerHTML = url
        ? `<img class="${mode === "snapshots" ? "snap" : "live"}" src="${url}" alt="camera">`
        : `<div class="empty">Camera entity not available</div>`;
      this._viewMode = mode;
      if (mode === "snapshots") {
        const interval = Math.max(2, Math.min(30, this._snapshotIntervalSeconds())) * 1000;
        this._startSnapshotLoop(interval);
      } else {
        this._stopSnapshotLoop();
      }
    }

    const [rx1, ry1, rx2, ry2] = d.roi || [0, 0, 1, 1];
    const roi = q("roi");
    roi.hidden = rx1 === 0 && ry1 === 0 && rx2 === 1 && ry2 === 1;
    Object.assign(roi.style, { left: `${rx1 * 100}%`, top: `${ry1 * 100}%`, width: `${(rx2 - rx1) * 100}%`, height: `${(ry2 - ry1) * 100}%` });

    q("dets").innerHTML =
      mode === "snapshots"
        ? (s.detections || [])
            .filter((x) => x.class_id === 0)
            .map(
              (x) =>
                `<div class="det" style="left:${x.box[0] * 100}%;top:${x.box[1] * 100}%;width:${(x.box[2] - x.box[0]) * 100}%;height:${(x.box[3] - x.box[1]) * 100}%"><span>${Math.round(x.confidence * 100)}%</span></div>`
            )
            .join("")
        : "";

    const banner = q("banner");
    if (s.connection_lost) {
      banner.className = "banner alarm";
      banner.textContent = "⚠ Camera connection lost while monitoring!";
    } else if (s.crying) {
      banner.className = "banner warn";
      banner.textContent = "👶 Crying detected";
    } else if (!s.camera_online) {
      banner.className = "banner warn";
      banner.textContent = `Camera offline${s.last_error ? ": " + s.last_error : ""}`;
    } else {
      banner.className = "banner";
      banner.textContent = "";
    }

    q("mode").value = mode;
    q("start").hidden = monitoring;
    q("stop").hidden = !monitoring;
    q("audio").textContent = this._audioOn ? "Stop audio" : "Listen";
    q("player").hidden = !this._audioOn;
    q("meta").textContent = `${d.camera_name} · last snapshot ${this._fmt(s.last_snapshot)} · presence model ${s.presence_model_ready ? "ready" : "missing"} · cry model ${s.cry_model_ready ? "ready" : "missing"}`;

    const dot = (id, cls) => (q(id).className = `dot ${cls}`);
    dot("dot-baby", s.baby_present ? "on" : "");
    q("txt-baby").textContent = `Baby in bed (${Math.round(s.presence_confidence * 100)}%)`;
    dot("dot-mon", monitoring ? "on" : "");
    q("txt-mon").textContent = `Monitoring: ${s.monitoring}`;
    dot("dot-cry", s.crying ? "alarm" : "");
    q("txt-cry").textContent = `Crying (${Math.round(s.cry_score * 100)}%)`;
    dot("dot-cam", s.connection_lost ? "alarm" : s.camera_online ? "on" : "warn");
    q("txt-cam").textContent = s.connection_lost ? "Connection alarm" : s.camera_online ? "Camera online" : "Camera offline";
    q("meta2").textContent = `Last detection ${this._fmt(s.last_detection)} · last cry ${this._fmt(s.last_cry)}`;

    main.querySelectorAll("input[data-key]").forEach((el) => {
      const id = ents[el.dataset.key];
      el.dataset.entity = id || "";
      el.disabled = !id;
      const st = id && this._hass.states[id];
      if (st) el.checked = st.state === "on";
    });

    const modeText =
      { off: "manual (nothing is downloaded)", once: "download once", auto: "download and keep updated" }[s.model_download_mode] ||
      s.model_download_mode;
    const upd =
      s.models_available_version && s.models_available_version !== s.models_version
        ? ` · update available: ${s.models_available_version}`
        : "";
    q("models").textContent =
      `Mode: ${modeText} · installed: ${s.models_version || "manual files"}${upd}` +
      (s.models_downloading ? " · downloading…" : "") +
      (this._dlError || s.models_error ? ` · error: ${this._dlError || s.models_error}` : "");
    q("dl").disabled = !!s.models_downloading;
    q("dl").textContent = s.models_downloading ? "Downloading…" : "Download / update models";

    const player = q("player");
    if (this._audioOn) {
      const src = `${d.audio_url}&n=${this._audioNonce}`;
      if (player.getAttribute("src") !== src) {
        player.setAttribute("src", src);
        player.play().catch(() => {});
      }
    }
  }

  _snapshotIntervalSeconds() {
    const id = this._data?.entities?.snapshot_interval;
    const st = id && this._hass.states[id];
    return st ? Number(st.state) || 5 : 5;
  }

  _toggleAudio() {
    this._audioOn = !this._audioOn;
    this._audioNonce = Date.now();
    if (!this._audioOn) {
      const player = this.shadowRoot.querySelector("#player");
      if (player) {
        player.pause();
        player.removeAttribute("src");
        player.load();
      }
    }
    this._render();
  }
}

customElements.define("babycam-panel", BabyCamPanel);
