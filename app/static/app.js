const { createApp } = Vue;
const APP_STATE_COOKIE_AGE_SECONDS = 60 * 60 * 24 * 365;

function setCookie(name, value, maxAgeSeconds = APP_STATE_COOKIE_AGE_SECONDS) {
  const encoded = encodeURIComponent(String(value));
  document.cookie = `${name}=${encoded}; Max-Age=${maxAgeSeconds}; Path=/; SameSite=Lax`;
}

function getCookie(name) {
  const key = `${name}=`;
  const parts = document.cookie.split(";").map((part) => part.trim());
  const match = parts.find((part) => part.startsWith(key));
  if (!match) return null;
  return decodeURIComponent(match.slice(key.length));
}

async function parseJsonSafely(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

createApp({
  data() {
    return {
      setupRequired: false,
      isAuthenticated: false,
      appVersion: "",
      message: "",
      route: "devices",
      theme: getCookie("theme") === "light" ? "light" : "dark",
      devices: [],
      selectedDevice: null,
      form: { device_name: "", raw_value: "", location: "", description: "" },
      editForm: { device_name: "", location: "", description: "" },
      filters: { q: getCookie("devices_filter_q") || "" },
      setup: { username: "", password: "", github_repo: "", github_token: "", github_branch: "main" },
      loginForm: { username: "", password: "" },
      scannerActive: false,
      scannerStream: null,
      scannerTimer: null,
      barcodeDetector: null,
      syncPreview: [],
      syncPreviewLoading: false,
      syncImporting: false,
      haTestLoading: false,
      _abortController: null,
      admin: {
        username: "",
        new_password: "",
        github_repo: "",
        github_token: "",
        github_token_masked: "",
        github_branch: "main",
        ha_url: "",
        ha_token: "",
        ha_token_masked: "",
        ha_mode: "ingress",
        ha_addon_slug: "zwavejs2mqtt",
        zwave_base_url: "",
        zwave_api_token: "",
        zwave_api_token_masked: "",
        ha_zwave_path: "/api/nodes",
        ha_verify_ssl: true,
        request_timeout_seconds: 10,
        retry_count: 3,
      },
    };
  },
  async mounted() {
    this.route = this.getRouteFromLocation();
    window.addEventListener("popstate", this.handlePopState);
    await this.bootstrap();
  },
  computed: {
    canScanWithCamera() {
      return !!(window.BarcodeDetector && navigator.mediaDevices?.getUserMedia);
    },
    syncPreviewCounts() {
      const counts = { "new": 0, update: 0, unchanged: 0, skip: 0 };
      for (const item of this.syncPreview || []) {
        if (item.action in counts) counts[item.action]++;
      }
      return counts;
    },
    syncSelectedCount() {
      return (this.syncPreview || []).filter((i) => i.selected).length;
    },
    allSyncItemsSelected() {
      const selectable = (this.syncPreview || []).filter((i) => i.action === "new" || i.action === "update");
      return selectable.length > 0 && selectable.every((i) => i.selected);
    },
  },
  methods: {
    setMessage(msg) {
      this.message = msg;
      setTimeout(() => {
        if (this.message === msg) this.message = "";
      }, 3000);
    },
    toggleTheme() {
      this.theme = this.theme === "dark" ? "light" : "dark";
      setCookie("theme", this.theme);
    },
    getRouteFromLocation() {
      const path = window.location.pathname || "/";
      if (path === "/profile") return "profile";
      return "devices";
    },
    handlePopState() {
      this.route = this.getRouteFromLocation();
      setCookie("route", this.route);
    },
    navigateTo(route) {
      const targetPath = route === "profile" ? "/profile" : "/";
      if (window.location.pathname !== targetPath) {
        window.history.pushState({}, "", targetPath);
      }
      this.route = route;
      setCookie("route", route);
    },
    openProfile() {
      if (!this.isAuthenticated) return;
      this.navigateTo("profile");
    },
    goToDevices() {
      this.navigateTo("devices");
    },
    async bootstrap() {
      const setupRes = await fetch("/api/v1/setup/status");
      const setupBody = await parseJsonSafely(setupRes);
      if (!setupRes.ok || !setupBody) {
        this.setMessage("Could not verify setup status");
        return;
      }
      this.setupRequired = !setupBody.setup_complete;
      if (this.setupRequired) return;
      await this.refreshSession();
    },
    async refreshSession() {
      const res = await fetch("/api/v1/auth/me");
      if (!res.ok) {
        this.isAuthenticated = false;
        return;
      }
      const body = await parseJsonSafely(res);
      this.isAuthenticated = !!body?.authenticated;
      if (!this.isAuthenticated) {
        return;
      }
      this.appVersion = body?.version || "";
      const settings = body?.settings || {};
      this.admin = {
        username: settings.username || "",
        new_password: "",
        github_repo: settings.github_repo || "",
        github_token: "",
        github_token_masked: settings.github_token_masked || "",
        github_branch: settings.github_branch || "main",
        ha_url: settings.ha_url || "",
        ha_token: "",
        ha_token_masked: settings.ha_token_masked || "",
        ha_mode: settings.ha_mode || "ingress",
        ha_addon_slug: settings.ha_addon_slug || "zwavejs2mqtt",
        zwave_base_url: settings.zwave_base_url || "",
        zwave_api_token: "",
        zwave_api_token_masked: settings.zwave_api_token_masked || "",
        ha_zwave_path: settings.ha_zwave_path || "/api/nodes",
        ha_verify_ssl: settings.ha_verify_ssl !== false,
        request_timeout_seconds: settings.request_timeout_seconds || 10,
        retry_count: settings.retry_count ?? 3,
      };
      await this.loadDevices();
    },
    async completeSetup() {
      const res = await fetch("/api/v1/setup/bootstrap", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(this.setup),
      });
      if (!res.ok) return this.setMessage("Setup failed");
      this.setupRequired = false;
      this.loginForm.username = this.setup.username;
      this.setMessage("Setup complete. Please login.");
    },
    async login() {
      const res = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(this.loginForm),
      });
      if (!res.ok) return this.setMessage("Invalid login");
      this.isAuthenticated = true;
      await this.refreshSession();
      this.setMessage("Logged in");
    },
    async logout() {
      await fetch("/api/v1/auth/logout", { method: "POST" });
      this.isAuthenticated = false;
      this.route = "devices";
      if (window.location.pathname !== "/") {
        window.history.pushState({}, "", "/");
      }
    },
    async loadDevices() {
      const params = new URLSearchParams();
      if (this.filters.q) params.set("q", this.filters.q);
      setCookie("devices_filter_q", this.filters.q || "");
      const res = await fetch(`/api/v1/devices?${params.toString()}`);
      if (res.status === 401) {
        this.isAuthenticated = false;
        this.devices = [];
        return;
      }
      if (!res.ok) return;
      const body = await parseJsonSafely(res);
      this.devices = body?.items || [];
    },
    openAddModal() {
      this.form = { device_name: "", raw_value: "", location: "", description: "" };
      this.$refs.addModal.showModal();
    },
    async toggleScanner() {
      if (this.scannerActive) {
        this.stopScanner();
        return;
      }
      await this.startScanner();
    },
    async startScanner() {
      if (!this.canScanWithCamera) {
        this.setMessage("Camera scan is not supported on this browser");
        return;
      }
      this.stopScanner();
      const videoEl = this.$refs.scannerVideo;
      if (!videoEl) return;
      const supportedFormats = await BarcodeDetector.getSupportedFormats();
      if (!supportedFormats.includes("qr_code")) {
        this.setMessage("QR format is not supported by this camera scanner");
        return;
      }
      this.barcodeDetector = new BarcodeDetector({ formats: ["qr_code"] });
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: "environment" },
        },
        audio: false,
      });
      this.scannerStream = stream;
      videoEl.srcObject = stream;
      await videoEl.play();
      this.scannerActive = true;
      this.scannerTimer = setInterval(async () => {
        if (!this.scannerActive || !this.barcodeDetector) return;
        try {
          const codes = await this.barcodeDetector.detect(videoEl);
          if (!codes.length) return;
          const value = (codes[0].rawValue || "").trim();
          if (!value) return;
          this.form.raw_value = value;
          this.stopScanner();
          this.setMessage("QR scanned and payload filled");
        } catch {
          this.stopScanner();
          this.setMessage("Camera scan failed");
        }
      }, 450);
    },
    stopScanner() {
      this.scannerActive = false;
      if (this.scannerTimer) {
        clearInterval(this.scannerTimer);
        this.scannerTimer = null;
      }
      if (this.scannerStream) {
        this.scannerStream.getTracks().forEach((track) => track.stop());
        this.scannerStream = null;
      }
      const videoEl = this.$refs.scannerVideo;
      if (videoEl) {
        videoEl.srcObject = null;
      }
      this.barcodeDetector = null;
    },
    async addDevice() {
      const res = await fetch("/api/v1/devices", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(this.form),
      });
      if (!res.ok) return this.setMessage("Could not add device");
      this.stopScanner();
      this.$refs.addModal.close();
      await this.loadDevices();
      this.setMessage("Device added");
    },
    openDevice(item) {
      this.selectedDevice = item;
      this.editForm = {
        device_name: item.device_name,
        location: item.location || "",
        description: item.description || "",
      };
      this.$refs.deviceModal.showModal();
    },
    async saveDeviceEdits() {
      if (!this.selectedDevice) return;
      const res = await fetch(`/api/v1/devices/${this.selectedDevice.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(this.editForm),
      });
      if (!res.ok) return this.setMessage("Update failed");
      await this.loadDevices();
      this.$refs.deviceModal.close();
      this.setMessage("Device updated");
    },
    async remove(id) {
      const res = await fetch(`/api/v1/devices/${id}`, { method: "DELETE" });
      if (!res.ok) return this.setMessage("Delete failed");
      this.$refs.deviceModal.close();
      await this.loadDevices();
      this.setMessage("Device deleted");
    },
    async saveSettings() {
      const payload = {
        username: this.admin.username,
        new_password: this.admin.new_password || null,
        github_repo: this.admin.github_repo,
        github_token: this.admin.github_token.trim() || undefined,
        github_branch: this.admin.github_branch,
        ha_url: this.admin.ha_url || null,
        ha_token: this.admin.ha_token.trim() || undefined,
        ha_mode: this.admin.ha_mode || "ingress",
        ha_addon_slug: this.admin.ha_addon_slug || "zwavejs2mqtt",
        zwave_base_url: this.admin.zwave_base_url || null,
        zwave_api_token: this.admin.zwave_api_token.trim() || undefined,
        ha_zwave_path: this.admin.ha_zwave_path || "/api/nodes",
        ha_verify_ssl: this.admin.ha_verify_ssl,
        request_timeout_seconds: this.admin.request_timeout_seconds || 10,
        retry_count: Number.isFinite(this.admin.retry_count) ? this.admin.retry_count : 3,
      };
      const res = await fetch("/api/v1/admin/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) return this.setMessage("Failed to save settings");
      const body = await parseJsonSafely(res);
      this.admin.new_password = "";
      this.admin.github_token = "";
      this.admin.ha_token = "";
      this.admin.zwave_api_token = "";
      this.admin.github_token_masked = body?.settings?.github_token_masked || this.admin.github_token_masked;
      this.admin.ha_token_masked = body?.settings?.ha_token_masked || this.admin.ha_token_masked;
      this.admin.zwave_api_token_masked = body?.settings?.zwave_api_token_masked || this.admin.zwave_api_token_masked;
      this.setMessage("Settings saved");
    },
    async testHomeAssistantConfig() {
      if (this.haTestLoading) return;
      this.haTestLoading = true;
      this.setMessage("Testing HA connection...");
      const payload = {
        ha_url: this.admin.ha_url || null,
        ha_token: this.admin.ha_token.trim() || undefined,
        ha_mode: this.admin.ha_mode || "ingress",
        ha_addon_slug: this.admin.ha_addon_slug || "zwavejs2mqtt",
        zwave_base_url: this.admin.zwave_base_url || null,
        zwave_api_token: this.admin.zwave_api_token.trim() || undefined,
        ha_zwave_path: this.admin.ha_zwave_path || "/api/nodes",
        ha_verify_ssl: this.admin.ha_verify_ssl,
        request_timeout_seconds: this.admin.request_timeout_seconds || 10,
        retry_count: Number.isFinite(this.admin.retry_count) ? this.admin.retry_count : 3,
      };
      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 30000);
        const res = await fetch("/api/v1/admin/test-home-assistant-config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          signal: controller.signal,
        });
        clearTimeout(timeout);
        const body = await parseJsonSafely(res);
        if (!res.ok) return this.setMessage("Config test failed");
        this.setMessage(body?.ok ? `HA config OK — found ${body?.count || 0} node${body?.count === 1 ? '' : 's'}` : `HA config failed: ${body?.reason || "unknown"}`);
      } catch (err) {
        if (err.name === "AbortError") {
          this.setMessage("HA config test timed out — check URL and network");
        } else {
          this.setMessage("HA config test failed — could not reach server");
        }
      } finally {
        this.haTestLoading = false;
      }
    },
    cancelSyncPreview() {
      if (this._abortController) {
        this._abortController.abort();
        this._abortController = null;
      }
      this.syncPreviewLoading = false;
      this.$refs.syncPreviewModal.close();
    },
    async syncFromHomeAssistant() {
      this.syncPreview = [];
      this.syncPreviewLoading = true;
      this.syncImporting = false;
      this._abortController = new AbortController();
      this.$refs.syncPreviewModal.showModal();
      try {
        const res = await fetch("/api/v1/admin/preview-home-assistant-sync", {
          method: "POST",
          signal: this._abortController.signal,
        });
        const body = await parseJsonSafely(res);
        if (!res.ok) {
          this.$refs.syncPreviewModal.close();
          return this.setMessage(body?.detail || "Failed to fetch HA preview");
        }
        this.syncPreview = (body?.preview || []).map((item) => ({
          ...item,
          selected: item.action === "new" || item.action === "update",
        }));
      } catch (err) {
        this.$refs.syncPreviewModal.close();
        if (err.name === "AbortError") {
          this.setMessage("Sync preview cancelled");
        } else {
          this.setMessage("Failed to connect to Home Assistant");
        }
      } finally {
        this.syncPreviewLoading = false;
        this._abortController = null;
      }
    },
    toggleAllSyncItems() {
      const selectable = (this.syncPreview || []).filter((i) => i.action === "new" || i.action === "update");
      const allSelected = selectable.every((i) => i.selected);
      selectable.forEach((i) => (i.selected = !allSelected));
    },
    async confirmSync() {
      this.syncImporting = true;
      try {
        const res = await fetch("/api/v1/admin/sync-from-home-assistant", { method: "POST" });
        const body = await parseJsonSafely(res);
        if (!res.ok) {
          return this.setMessage(body?.detail || "Home Assistant sync failed");
        }
        const results = body?.results || {};
        await this.loadDevices();
        this.$refs.syncPreviewModal.close();
        this.setMessage(`HA sync complete: +${results.created || 0} new, ${results.updated || 0} updated, ${results.skipped || 0} skipped`);
      } catch {
        this.setMessage("Sync request failed");
      } finally {
        this.syncImporting = false;
      }
    },
    async testRepoAuth() {
      const res = await fetch("/api/v1/admin/test-repo-auth", { method: "POST" });
      const body = await parseJsonSafely(res);
      this.setMessage(body?.ok ? "Repo auth OK" : `Repo auth failed: ${body?.reason || "Unknown error"}`);
    },
    async forcePull() {
      const res = await fetch("/api/v1/admin/force-pull-update", { method: "POST" });
      const body = await parseJsonSafely(res);
      this.setMessage(body?.ok ? "Repository updated" : "Repository update failed");
    },
  },
  beforeUnmount() {
    window.removeEventListener("popstate", this.handlePopState);
    this.stopScanner();
  },
}).mount("#app");
