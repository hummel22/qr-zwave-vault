const { createApp } = Vue;

createApp({
  data() {
    return {
      devices: [],
      form: { device_name: "", raw_value: "", location: "", description: "" },
      filters: { q: "" },
      qrSrc: "",
    };
  },
  async mounted() {
    await this.loadDevices();
  },
  methods: {
    async loadDevices() {
      const params = new URLSearchParams();
      if (this.filters.q) params.set("q", this.filters.q);
      const res = await fetch(`/api/v1/devices?${params.toString()}`);
      const body = await res.json();
      this.devices = body.items;
    },
    async addDevice() {
      await fetch("/api/v1/devices", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(this.form),
      });
      this.form = { device_name: "", raw_value: "", location: "", description: "" };
      await this.loadDevices();
    },
    async remove(id) {
      await fetch(`/api/v1/devices/${id}`, { method: "DELETE" });
      await this.loadDevices();
    },
    showQr(id) {
      this.qrSrc = `/api/v1/devices/${id}/qr.png`;
      this.$refs.modal.showModal();
    },
    async syncNow() {
      await fetch("/api/v1/sync", { method: "POST" });
      await this.loadDevices();
    },
  },
}).mount("#app");
