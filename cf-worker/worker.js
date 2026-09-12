// Dispara el workflow_dispatch de GitHub Actions cada 5 min via Cron Trigger.
// GitHub's propio `schedule:` no era confiable; Cloudflare Cron si lo es.
export default {
  async scheduled(event, env, ctx) {
    const res = await fetch(
      "https://api.github.com/repos/Jalbyte/boletas-ticketmaster/actions/workflows/monitor.yml/dispatches",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
          "User-Agent": "bts-ticket-cron",
        },
        body: JSON.stringify({ ref: "master" }),
      }
    );
    if (!res.ok) console.error("dispatch failed", res.status, await res.text());
  },
};
