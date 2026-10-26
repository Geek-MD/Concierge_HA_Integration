/* Concierge panel: native HA cards and a dependency-free monthly chart. */
class ConciergePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._request = 0;
  }

  set hass(hass) {
    this._hass = hass;
    this.shadowRoot.querySelectorAll("ha-menu-button").forEach(el => { el.hass = hass; });
    this._updateEntities();
    if (this.isConnected && !this._data && !this._loading) this._load();
  }

  connectedCallback() {
    this._load();
    this._timer = setInterval(() => this._load(), 30000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._request++;
    this._loading = false;
  }

  async _load() {
    if (!this._hass || this._loading) return;
    this._loading = true;
    const request = ++this._request;
    try {
      const data = await this._hass.callWS({ type: "concierge_ha_integration/panel_data" });
      if (!this.isConnected || request !== this._request) return;
      const serialized = JSON.stringify(data);
      if (serialized !== this._serialized) {
        this._data = data;
        this._serialized = serialized;
        await this._render();
      }
    } catch (error) {
      if (this.isConnected && request === this._request) {
        this._serialized = null;
        this._shell();
        this._text(this._main, "p", "No se pudo cargar Concierge. Se reintentará automáticamente.");
      }
    } finally {
      if (request === this._request) this._loading = false;
    }
  }

  _text(parent, tag, text) {
    const el = document.createElement(tag);
    el.textContent = text;
    parent.append(el);
    return el;
  }

  _shell() {
    this.shadowRoot.innerHTML = `<style>
      :host { display:block; height:100%; overflow:auto; color:var(--primary-text-color); background:var(--primary-background-color); font-family:var(--paper-font-body1_-_font-family, sans-serif); }
      header { display:flex; align-items:center; padding:8px 16px; background:var(--app-header-background-color); color:var(--app-header-text-color); }
      h1 { font-size:20px; } main { max-width:1300px; margin:auto; padding:24px; }
      h2 { margin-top:28px; } .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr)); gap:20px; }
      article { padding:20px; border-radius:var(--ha-card-border-radius,12px); border:1px solid var(--divider-color); background:var(--ha-card-background,var(--card-background-color)); }
      h3 { margin:0 0 12px; } .total { font-size:26px; font-weight:600; margin:8px 0; }
      p, small { color:var(--secondary-text-color); } .chart { display:flex; align-items:end; height:170px; gap:6px; margin-top:22px; }
      .column { flex:1; min-width:0; height:100%; display:flex; flex-direction:column; justify-content:end; align-items:center; gap:6px; }
      .bar { width:100%; min-height:3px; background:var(--primary-color); border-radius:4px 4px 0 0; }
      .missing { background:var(--divider-color); } .label { font-size:10px; writing-mode:vertical-rl; }
      details { margin-top:16px; } table { width:100%; border-collapse:collapse; margin-top:12px; } td,th { text-align:left; padding:6px; border-bottom:1px solid var(--divider-color); }
      .entities { margin-bottom:20px; } .entity { display:flex; width:100%; justify-content:space-between; gap:16px; padding:12px 0; border:0; border-bottom:1px solid var(--divider-color); background:transparent; color:inherit; text-align:left; cursor:pointer; font:inherit; }
      .entity span:last-child { text-align:right; }
      @media(max-width:500px) { main { padding:12px; } article { padding:14px; } }
    </style><header><ha-menu-button></ha-menu-button><h1>Concierge</h1></header><main></main>`;
    this._main = this.shadowRoot.querySelector("main");
    this.shadowRoot.querySelector("ha-menu-button").hass = this._hass;
  }

  async _render() {
    if (!this.isConnected) return;
    this._shell();
    const money = value => new Intl.NumberFormat(this._hass.locale?.language || "es-CL", {
      style: "currency", currency: "CLP", maximumFractionDigits: 0
    }).format(value);
    this._text(this._main, "h2", "Costos de los últimos 12 meses");
    this._text(this._main, "p", "Totales de boletas por mes de emisión. Los meses sin registros se muestran como sin datos.");
    const grid = document.createElement("section");
    grid.className = "grid";
    this._main.append(grid);
    if (!this._data.services.length) this._text(grid, "p", "Agrega un servicio en la configuración de Concierge para comenzar.");
    for (const service of this._data.services) {
      const card = document.createElement("article");
      grid.append(card);
      this._text(card, "h3", service.name);
      const known = service.months.filter(month => month.amount !== null);
      this._text(card, "div", known.length ? money(known.reduce((sum, month) => sum + month.amount, 0)) : "Sin datos").className = "total";
      this._text(card, "small", `Total registrado · ${known.length} de 12 meses con datos`);
      const chart = document.createElement("div");
      chart.className = "chart";
      chart.setAttribute("role", "img");
      chart.setAttribute("aria-label", `Costos mensuales de ${service.name}; valores disponibles en la tabla de detalle`);
      card.append(chart);
      const max = Math.max(1, ...known.map(month => Math.abs(month.amount)));
      const details = document.createElement("details");
      this._text(details, "summary", "Ver montos mensuales");
      const table = document.createElement("table");
      const heading = document.createElement("tr");
      this._text(heading, "th", "Mes");
      this._text(heading, "th", "Total");
      table.append(heading);
      for (const month of service.months) {
        const label = new Intl.DateTimeFormat(this._hass.locale?.language || "es", { month: "short", year: "2-digit" }).format(new Date(`${month.month}-15T12:00:00`));
        const value = month.amount === null ? "Sin datos" : money(month.amount);
        const column = document.createElement("div");
        column.className = "column";
        column.title = `${label}: ${value}`;
        const bar = document.createElement("div");
        bar.className = `bar${month.amount === null ? " missing" : ""}`;
        bar.style.height = `${month.amount === null ? 2 : Math.max(2, Math.abs(month.amount) / max * 125)}px`;
        column.append(bar);
        this._text(column, "span", label).className = "label";
        chart.append(column);
        const row = document.createElement("tr");
        this._text(row, "td", label);
        this._text(row, "td", value);
        table.append(row);
      }
      details.append(table);
      card.append(details);
    }
    this._text(this._main, "h2", "Entidades por servicio");
    for (const service of this._data.services) {
      const enabled = service.entities.filter(entity => !entity.disabled);
      const card = document.createElement("article");
      card.className = "entities";
      this._text(card, "h3", service.name);
      for (const entity of enabled) {
        const row = document.createElement("button");
        row.className = "entity";
        row.dataset.entityId = entity.entity_id;
        row.append(document.createElement("span"), document.createElement("span"));
        row.addEventListener("click", () => this.dispatchEvent(new CustomEvent("hass-more-info", {
          detail: { entityId: entity.entity_id }, bubbles: true, composed: true
        })));
        card.append(row);
      }
      this._main.append(card);
      const disabled = service.entities.filter(entity => entity.disabled);
      if (disabled.length) {
        const details = document.createElement("details");
        this._text(details, "summary", `Entidades deshabilitadas (${disabled.length})`);
        const list = document.createElement("ul");
        disabled.forEach(entity => this._text(list, "li", entity.entity_id));
        details.append(list);
        this._main.append(details);
      }
    }
    this._updateEntities();
  }

  _updateEntities() {
    this.shadowRoot.querySelectorAll("[data-entity-id]").forEach(row => {
      const id = row.dataset.entityId;
      const state = this._hass?.states[id];
      row.children[0].textContent = state?.attributes.friendly_name || id;
      row.children[1].textContent = state ? (this._hass.formatEntityState?.(state) || state.state) : "No disponible";
    });
  }
}
if (!customElements.get("concierge-panel")) customElements.define("concierge-panel", ConciergePanel);
