const { createApp } = Vue;

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
      message: "",
      theme: localStorage.getItem("theme") || "dark",
      devices: [],
      selectedDevice: null,
      form: { device_name: "", raw_value: "", location: "", description: "" },
      editForm: { device_name: "", location: "", description: "" },
      filters: { q: "" },
      setup: { username: "", password: "", github_repo: "", github_token: "", github_branch: "main" },
      loginForm: { username: "", password: "" },
      scannerActive: false,
      scannerStream: null,
      scannerTimer: null,
      barcodeDetector: null,
      admin: {
        username: "",
        new_password: "",
        github_repo: "",
        github_token: "",
        github_branch: "main",
        ha_url: "",
        ha_token: "",
        ha_zwave_path: "/api/nodes",
        ha_verify_ssl: true,
      },
    };
  },
  async mounted() {
    await this.bootstrap();
  },
  computed: {
    canScanWithCamera() {
      return !!(window.BarcodeDetector && navigator.mediaDevices?.getUserMedia);
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
      localStorage.setItem("theme", this.theme);
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
      const settings = body?.settings || {};
      this.admin = {
        username: settings.username || "",
        new_password: "",
        github_repo: settings.github_repo || "",
        github_token: "",
        github_branch: settings.github_branch || "main",
        ha_url: settings.ha_url || "",
        ha_token: "",
        ha_zwave_path: settings.ha_zwave_path || "/api/nodes",
        ha_verify_ssl: settings.ha_verify_ssl !== false,
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
      this.$refs.adminModal.close();
    },
    async loadDevices() {
      const params = new URLSearchParams();
      if (this.filters.q) params.set("q", this.filters.q);
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
    openAdmin() {
      if (!this.isAuthenticated) return;
      this.$refs.adminModal.showModal();
    },
    async saveSettings() {
      const payload = {
        username: this.admin.username,
        new_password: this.admin.new_password || null,
        github_repo: this.admin.github_repo,
        github_token: this.admin.github_token || null,
        github_branch: this.admin.github_branch,
        ha_url: this.admin.ha_url || null,
        ha_token: this.admin.ha_token || null,
        ha_zwave_path: this.admin.ha_zwave_path || "/api/nodes",
        ha_verify_ssl: this.admin.ha_verify_ssl,
      };
      const res = await fetch("/api/v1/admin/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) return this.setMessage("Failed to save settings");
      this.admin.new_password = "";
      this.admin.github_token = "";
      this.admin.ha_token = "";
      this.setMessage("Settings saved");
    },
    async testHomeAssistantConfig() {
      const res = await fetch("/api/v1/admin/test-home-assistant-config", { method: "POST" });
      const body = await parseJsonSafely(res);
      if (!res.ok) return this.setMessage("Config test failed");
      this.setMessage(body?.ok ? `Home Assistant config OK (${body?.count || 0} nodes)` : `HA config failed: ${body?.reason || "unknown"}`);
    },
    async syncFromHomeAssistant() {
      const res = await fetch("/api/v1/admin/sync-from-home-assistant", { method: "POST" });
      const body = await parseJsonSafely(res);
      if (!res.ok) {
        return this.setMessage(body?.detail || "Home Assistant sync failed");
      }
      const results = body?.results || {};
      await this.loadDevices();
      this.setMessage(`HA sync done: +${results.created || 0} new, ${results.updated || 0} updated, ${results.skipped || 0} skipped`);
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
    this.stopScanner();
  },
}).mount("#app");
