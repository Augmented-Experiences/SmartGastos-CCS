module.exports = {
  title: "SmartGastos — Cámara de Comercio de Santiago",
  description: "Gestión de gastos y contabilidad con IA local para PyMEs — 100% offline | Cámara de Comercio de Santiago",
  icon: "icon.png",
  version: "1.5.0",

  menu: async (kernel, info) => {
    // Verificar instalación usando API de Pinokio (cross-platform)
    const installed = await kernel.exists(__dirname, "venv")

    // Verificar si está corriendo usando API de Pinokio
    const running = await kernel.script.running(__dirname, "start.json")

    // ---- Estado: No instalado ----
    if (!installed) {
      return [{
        default: true,
        icon: "fa-solid fa-download",
        text: "Instalar",
        href: "install.json",
        description: "Instalación automática con 1 click (5-15 min)"
      }]
    }

    // ---- Estado: Corriendo ----
    if (running) {
      return [
        {
          icon: "fa-solid fa-circle",
          text: "En ejecución",
          href: "start.json",
          style: "color: #3DAE2B"
        },
        {
          icon: "fa-solid fa-stop",
          text: "Detener",
          href: "stop.json"
        }
      ]
    }

    // ---- Estado: Instalado pero no corriendo ----
    return [
      {
        default: true,
        icon: "fa-solid fa-play",
        text: "Iniciar",
        href: "start.json",
        description: "Iniciar el plugin"
      },
      {
        icon: "fa-solid fa-rotate",
        text: "Reinstalar",
        href: "install.json",
        description: "Reinstalar si hay problemas"
      }
    ]
  }
}
