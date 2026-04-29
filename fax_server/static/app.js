(function () {
  const statusPill = document.querySelector("[data-fs-status]");
  const detail = document.querySelector("[data-monitor-detail]");
  const monitorStatus = document.querySelector("[data-monitor-status]");
  const systemFs = document.querySelector("[data-system-fs]");
  const monitorGateway = document.querySelector("[data-monitor-gateway]");
  const monitorCalls = document.querySelector("[data-monitor-calls]");
  const gatewayState = document.querySelector("[data-gateway-state]");
  const gatewayRegistration = document.querySelector("[data-gateway-registration]");
  const webexStatus = document.querySelector("[data-webex-status]");
  const webexToken = document.querySelector("[data-webex-token]");
  const webexOrg = document.querySelector("[data-webex-org]");
  const jobsBody = document.querySelector("[data-jobs-body]");
  const routesBody = document.querySelector("[data-routes-body]");
  const routeForm = document.querySelector("[data-route-form]");
  const routeStatus = document.querySelector("[data-route-status]");
  const routeEditStatus = document.querySelector("[data-route-edit-status]");
  const webexForm = document.querySelector("[data-webex-form]");
  const webexFormStatus = document.querySelector("[data-webex-form-status]");
  const destinationForm = document.querySelector("[data-destination-form]");
  const destinationStatus = document.querySelector("[data-destination-status]");
  const lastUpdated = document.querySelector("[data-last-updated]");
  const refreshButton = document.querySelector("[data-refresh]");
  const pageTitle = document.querySelector("[data-page-title]");

  const viewTitles = {
    dashboard: "Dashboard",
    "provider-provisioning": "Provider Provisioning",
    "destination-configuration": "Destination Configuration",
    send: "Send Fax",
    "fax-jobs": "Fax Jobs",
  };

  function text(value) {
    return value === null || value === undefined || value === "" ? "" : String(value);
  }

  function countActiveCalls(raw) {
    const match = text(raw).match(/(\d+)\s+total\./);
    return match ? match[1] : "0";
  }

  function setBadge(el, state, label) {
    if (!el) return;
    el.textContent = label;
    el.classList.toggle("ok", state === "ok");
    el.classList.toggle("error", state === "error");
  }

  function setStatus(ok, label) {
    if (!statusPill) return;
    statusPill.textContent = label;
    statusPill.classList.toggle("ok", ok);
    statusPill.classList.toggle("error", !ok);
  }

  function selectView(name) {
    const target = viewTitles[name] ? name : "dashboard";
    document.querySelectorAll("[data-view]").forEach(function (view) {
      view.classList.toggle("is-active", view.dataset.view === target);
    });
    document.querySelectorAll("[data-view-link]").forEach(function (link) {
      link.classList.toggle("active", link.dataset.viewLink === target);
    });
    if (pageTitle) pageTitle.textContent = viewTitles[target];
  }

  function initNavigation() {
    document.querySelectorAll("[data-view-link]").forEach(function (link) {
      link.addEventListener("click", function (event) {
        event.preventDefault();
        const target = link.dataset.viewLink;
        window.history.replaceState(null, "", `#${target}`);
        selectView(target);
      });
    });
    selectView((window.location.hash || "#dashboard").slice(1));
  }

  function renderStats(stats) {
    if (!stats) return;
    Object.keys(stats).forEach(function (key) {
      const el = document.querySelector(`[data-stat="${key}"]`);
      if (el) el.textContent = stats[key];
    });
  }

  function renderJobs(jobs) {
    if (!jobsBody || !Array.isArray(jobs)) return;
    jobsBody.innerHTML = jobs.map(function (job) {
      const sendAction = ["ready", "send_failed"].indexOf(job.status) !== -1
        ? `<form action="/faxes/${job.id}/send" method="post"><button type="submit" class="button button-small">Send</button></form>`
        : "";
      const fileAction = job.tiff_path
        ? `<a class="button button-small button-secondary" href="/faxes/${job.id}/file">File</a>`
        : "";
      return `
        <tr>
          <td>${job.id}</td>
          <td><span class="badge">${text(job.direction)}</span></td>
          <td><span class="badge badge-status" data-status="${text(job.status)}">${text(job.status)}</span></td>
          <td>${text(job.from_number)}</td>
          <td>${text(job.to_number)}</td>
          <td>${text(job.updated_at)}</td>
          <td class="mono">${text(job.freeswitch_uuid)}</td>
          <td class="actions-cell">${sendAction}${fileAction}</td>
        </tr>
      `;
    }).join("");
  }

  function renderRoutes(routes) {
    if (!routesBody || !Array.isArray(routes)) return;
    routesBody.innerHTML = routes.map(function (route) {
      return `
        <tr data-route-row data-route-id="${route.id}">
          <td>
            <input name="display_name" value="${escapeAttribute(route.display_name)}" required>
            <input name="webex_line_id" type="hidden" value="${escapeAttribute(route.webex_line_id)}">
            <input name="did_number" type="hidden" value="${escapeAttribute(route.did_number)}">
            <input name="extension" type="hidden" value="${escapeAttribute(route.extension)}">
            <input name="webex_workspace_id" type="hidden" value="${escapeAttribute(route.webex_workspace_id)}">
            <input name="webex_gateway_id" type="hidden" value="${escapeAttribute(route.webex_gateway_id)}">
            <input name="notes" type="hidden" value="${escapeAttribute(route.notes)}">
          </td>
          <td class="mono">${text(route.webex_line_id)}</td>
          <td>${text(route.did_number)}</td>
          <td>${text(route.extension)}</td>
          <td>
            <select name="destination_type">
              ${destinationOption("local", "Local inbox", route.destination_type)}
              ${destinationOption("email", "Email", route.destination_type)}
              ${destinationOption("webex_bot", "Webex Bot", route.destination_type)}
              ${destinationOption("teams_bot", "MS Teams Bot", route.destination_type)}
              ${destinationOption("webhook", "Webhook", route.destination_type)}
            </select>
          </td>
          <td><input name="destination_value" value="${escapeAttribute(route.destination_value)}" placeholder="address, room ID, webhook URL"></td>
          <td><label class="switch-row"><input name="enabled" type="checkbox" ${route.enabled ? "checked" : ""}><span>Enabled</span></label></td>
          <td><button type="button" class="button button-small" data-save-route>Save</button></td>
        </tr>
      `;
    }).join("");
  }

  function destinationOption(value, label, selected) {
    return `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`;
  }

  function escapeAttribute(value) {
    return text(value)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function gatewayLabel(raw) {
    if (!raw) return "Unknown";
    if (raw.indexOf("REGED") !== -1) return "Registered";
    if (raw.indexOf("FAIL") !== -1 || raw.indexOf("DOWN") !== -1) return "Attention";
    return "Visible";
  }

  async function refreshMonitor() {
    try {
      const response = await fetch("/api/monitor", { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`Monitor returned ${response.status}`);
      const payload = await response.json();
      const fs = payload.freeswitch || {};
      const webex = payload.webex || {};
      const ok = fs.status === "ok";
      const activeCalls = countActiveCalls(fs.calls);
      const gatewayOk = text(fs.gateway_status).indexOf("REGED") !== -1;

      setStatus(ok, ok ? "FreeSWITCH Online" : "FreeSWITCH Unreachable");
      setBadge(monitorStatus, ok ? "ok" : "error", ok ? "Online" : "Unreachable");
      if (systemFs) systemFs.textContent = ok ? "Ready" : "Unreachable";
      if (monitorGateway) monitorGateway.textContent = fs.gateway || "Unknown";
      if (monitorCalls) monitorCalls.textContent = activeCalls;
      setBadge(gatewayState, gatewayOk ? "ok" : "error", gatewayLabel(fs.gateway_status));
      if (gatewayRegistration) gatewayRegistration.textContent = gatewayLabel(fs.gateway_status);

      setBadge(webexStatus, webex.status === "configured" ? "ok" : "error", webex.status === "configured" ? "Configured" : "Missing token");
      if (webexToken) webexToken.textContent = webex.status === "configured" ? "Present" : "Not configured";
      if (webexOrg) webexOrg.textContent = webex.org_id || "Default organization";

      if (detail) {
        detail.textContent = ok
          ? [fs.gateway_status, "", fs.calls].filter(Boolean).join("\n")
          : fs.error || "FreeSWITCH monitor unavailable";
      }
      renderStats(payload.stats);
      renderJobs(payload.jobs);
      renderRoutes(payload.inbound_routes);
      if (lastUpdated) lastUpdated.textContent = `Updated ${new Date().toLocaleTimeString()}`;
    } catch (error) {
      setStatus(false, "Monitor Error");
      setBadge(monitorStatus, "error", "Error");
      if (systemFs) systemFs.textContent = "Monitor error";
      if (detail) detail.textContent = error.message || String(error);
    }
  }

  async function createRoute(event) {
    event.preventDefault();
    if (!routeForm) return;
    if (routeStatus) routeStatus.textContent = "Saving...";

    const body = Object.fromEntries(new FormData(routeForm).entries());
    try {
      const response = await fetch("/api/inbound-routes", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `Route create returned ${response.status}`);
      routeForm.reset();
      if (routeStatus) routeStatus.textContent = "Route added";
      refreshMonitor();
    } catch (error) {
      if (routeStatus) routeStatus.textContent = error.message || String(error);
    }
  }

  async function provisionFromWebex(event) {
    event.preventDefault();
    if (!webexForm) return;
    if (webexFormStatus) webexFormStatus.textContent = "Reading Webex...";

    const body = Object.fromEntries(new FormData(webexForm).entries());
    try {
      const response = await fetch("/api/webex/provision-gateway", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `Provision returned ${response.status}`);
      const reload = payload.reload && payload.reload.attempted
        ? payload.reload.ok ? "FreeSWITCH reloaded" : "FreeSWITCH reload failed"
        : "Dialplan written";
      if (webexFormStatus) webexFormStatus.textContent = `Provisioned ${payload.routes.length} line(s). ${reload}.`;
      refreshMonitor();
    } catch (error) {
      if (webexFormStatus) webexFormStatus.textContent = error.message || String(error);
    }
  }

  function rowPayload(row) {
    const form = new FormData();
    row.querySelectorAll("input, select").forEach(function (input) {
      if (input.type === "checkbox") {
        form.set(input.name, input.checked);
      } else {
        form.set(input.name, input.value);
      }
    });
    return Object.fromEntries(form.entries());
  }

  async function saveRoute(button) {
    const row = button.closest("[data-route-row]");
    if (!row) return;
    if (routeEditStatus) routeEditStatus.textContent = "Saving route...";
    try {
      const response = await fetch(`/api/inbound-routes/${row.dataset.routeId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(rowPayload(row)),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `Route update returned ${response.status}`);
      if (routeEditStatus) routeEditStatus.textContent = "Route updated";
      refreshMonitor();
    } catch (error) {
      if (routeEditStatus) routeEditStatus.textContent = error.message || String(error);
    }
  }

  async function saveDestinationSettings(event) {
    event.preventDefault();
    if (!destinationForm) return;
    if (destinationStatus) destinationStatus.textContent = "Saving...";

    const body = {};
    destinationForm.querySelectorAll("input").forEach(function (input) {
      body[input.name] = input.type === "checkbox" ? input.checked : input.value;
    });
    try {
      const response = await fetch("/api/destination-settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `Destination settings returned ${response.status}`);
      if (destinationStatus) destinationStatus.textContent = "Destination configuration saved";
    } catch (error) {
      if (destinationStatus) destinationStatus.textContent = error.message || String(error);
    }
  }

  initNavigation();
  if (webexForm) webexForm.addEventListener("submit", provisionFromWebex);
  if (routeForm) routeForm.addEventListener("submit", createRoute);
  if (destinationForm) destinationForm.addEventListener("submit", saveDestinationSettings);
  document.addEventListener("click", function (event) {
    const button = event.target.closest("[data-save-route]");
    if (button) saveRoute(button);
  });
  if (refreshButton) refreshButton.addEventListener("click", refreshMonitor);
  refreshMonitor();
  window.setInterval(refreshMonitor, 10000);
})();
