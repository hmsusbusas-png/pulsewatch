import { writeFileSync } from "node:fs";

const [base = "http://localhost:8000", email = "demo@example.com", password] =
  process.argv.slice(2);
if (!password) throw new Error("usage: node e2e/capture-fixtures.mjs <api> <email> <password>");

const login = await fetch(`${base}/api/auth/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email, password }),
});
const { access_token: token } = await login.json();
const headers = { Authorization: `Bearer ${token}` };
const get = async (path) => {
  const response = await fetch(`${base}/api${path}`, { headers });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
};

const fixtures = {};
const record = async (path) => {
  fixtures[path] = await get(path);
  return fixtures[path];
};

for (const path of [
  "/me",
  "/dashboard/summary",
  "/billing/subscription",
  "/billing/payments",
  "/plans",
  "/channels?cursor=0&limit=100",
  "/status-pages?cursor=0&limit=100",
  "/telegram/status",
  "/regions",
  "/escalations",
  "/templates",
  "/sites/daily?days=90",
  "/account/sessions?cursor=0&limit=100",
  "/account/audit?limit=30&cursor=0",
  "/keys",
  "/admin/stats",
  "/admin/users?limit=100&search=",
  "/incidents?limit=50&cursor=0",
  "/incidents?limit=50&cursor=0&state=open",
  "/health",
]) {
  await record(path);
}
const sites = await record("/sites?cursor=0&limit=100");
const api = sites.find((site) => site.name === "Публичный API") ?? sites[0];
for (const site of sites) {
  await record(`/sites/${site.id}`);
  await record(`/sites/${site.id}/incidents?limit=50`);
  await record(`/sites/${site.id}/daily?days=90`);
  await record(`/sites/${site.id}/slo`);
  await record(`/sites/${site.id}/stats?range=24h`);
  await record(`/sites/${site.id}/checks?range=24h&order=desc&limit=50&cursor=0`);
}
for (const range of ["7d", "30d", "90d"]) await record(`/sites/${api.id}/stats?range=${range}`);
for (const incident of fixtures["/incidents?limit=50&cursor=0"])
  await record(`/incidents/${incident.id}`);
for (const page of fixtures["/status-pages?cursor=0&limit=100"]) {
  await record(`/status-pages/${page.id}/announcements`);
  const response = await fetch(`${base}/api/public/pages/${page.slug}`);
  fixtures[`/public/pages/${page.slug}`] = await response.json();
}
for (const channel of fixtures["/channels?cursor=0&limit=100"])
  await record(`/channels/${channel.id}/deliveries`);

writeFileSync(new URL("./fixtures/demo.json", import.meta.url), `${JSON.stringify(fixtures)}\n`);
process.stdout.write(`captured ${Object.keys(fixtures).length} responses\n`);
