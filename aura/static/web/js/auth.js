/* Aura web — sign in, first account, and account security (password, two-factor, devices, activity). */
import { $, $$, h, I, api, state, events, toast, dialog, field } from "./core.js";

const ACTIONS = {
  login_ok: "Connexion", login_failed: "Échec de connexion", logout: "Déconnexion", owner_created: "Compte créé",
  upload: "Envoi", delete: "Suppression", mkdir: "Nouveau dossier", rename: "Renommage", move: "Déplacement",
  torrent_add: "Téléchargement lancé", torrent_pause: "Téléchargement en pause", torrent_resume: "Téléchargement repris",
  torrent_delete: "Téléchargement retiré", password_changed: "Mot de passe modifié", "2fa_enabled": "2FA activée",
  "2fa_disabled": "2FA désactivée", session_revoked: "Appareil déconnecté", nasdash_import: "Reprise de NAS Dashboard",
};

function setVersion(version) {
  $$("[data-version]").forEach((el) => { el.textContent = version ? `Aura ${version}` : ""; });
}

export async function check() {
  try {
    const response = await fetch("/api/me", { credentials: "same-origin" });
    const data = await response.json();
    setVersion(data.version);
    if (response.ok && data.authenticated) return data;
    showGate("", data);
  } catch {
    showGate("Le boîtier ne répond pas. Vérifie qu'il est allumé et sur le même réseau.");
  }
  return null;
}

export function showGate(message = "", context = null) {
  $("#app").hidden = true;
  $("#gate").hidden = false;
  const setup = Boolean(context && context.can_setup);
  $("#loginForm").hidden = setup;
  $("#setupForm").hidden = !setup;
  $(".gate-remote").hidden = Boolean(context && context.zone === "remote");
  const form = setup ? $("#setupForm") : $("#loginForm");
  const error = form.querySelector(".form-error");
  error.textContent = message;
  error.hidden = !message;
  setTimeout(() => form.querySelector("input").focus(), 60);
}

$$("[data-eye]").forEach((button) => button.addEventListener("click", () => {
  const input = button.parentElement.querySelector("input");
  input.type = input.type === "password" ? "text" : "password";
}));

async function submit(form, url, label, validate) {
  const button = form.querySelector("button[type=submit]");
  const error = form.querySelector(".form-error");
  error.hidden = true;
  const problem = validate ? validate(form) : "";
  if (problem) { error.textContent = problem; error.hidden = false; return; }
  button.disabled = true;
  button.textContent = "Vérification…";
  try {
    const body = new FormData(form);
    body.delete("confirm");
    const response = await fetch(url, { method: "POST", body, credentials: "same-origin" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (/chiffres/i.test(data.detail || "")) {
        $("#totpField").hidden = false;
        setTimeout(() => form.querySelector("[name=totp]").focus(), 60);
      }
      throw new Error(data.detail || `Connexion refusée (${response.status}).`);
    }
    // Proves the browser kept the session cookie: phones on plain HTTP used to drop a Secure cookie silently.
    const me = await fetch("/api/me", { credentials: "same-origin" }).then((r) => r.json()).catch(() => ({}));
    if (!me.authenticated) throw new Error("Identifiants corrects, mais le navigateur a refusé le cookie de session. Quitte la navigation privée ou ouvre le site en HTTPS.");
    form.reset();
    $("#totpField").hidden = true;
    events.emit("signed-in", me);
  } catch (exception) {
    error.textContent = exception.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

$("#loginForm").addEventListener("submit", (e) => { e.preventDefault(); submit(e.currentTarget, "/api/login", "Se connecter"); });
$("#setupForm").addEventListener("submit", (e) => {
  e.preventDefault();
  submit(e.currentTarget, "/api/setup-owner", "Créer le compte", (form) => {
    if (form.password.value.length < 12) return "Le mot de passe doit faire 12 caractères minimum.";
    if (form.password.value !== form.confirm.value) return "Les deux mots de passe diffèrent.";
    return "";
  });
});

export async function logout() {
  try { await api("/api/logout", { method: "POST" }); } catch { /* the session is gone either way */ }
  state.csrf = "";
  const context = await fetch("/api/me", { credentials: "same-origin" }).then((r) => r.json()).catch(() => null);
  showGate("", context);
}

/* ---------------------------------------------------------------- security */
export async function changePassword() {
  const current = field("Mot de passe actuel", { type: "password", autocomplete: "current-password" });
  const fresh = field("Nouveau mot de passe", { type: "password", autocomplete: "new-password", minlength: "12" });
  const confirm = field("Confirme", { type: "password", autocomplete: "new-password" });
  const body = h("div", {}, current.el, fresh.el, confirm.el);
  if (!await dialog({ title: "Changer mon mot de passe", text: "12 caractères minimum. Tes autres appareils seront déconnectés.", body, ok: "Enregistrer" })) return;
  if (fresh.input.value !== confirm.input.value) { toast("Les deux saisies diffèrent.", "err"); return; }
  try {
    await api("/api/account/password", { method: "POST", form: { current: current.input.value, new: fresh.input.value } });
    toast("Mot de passe mis à jour.", "ok");
  } catch (error) { toast(error.message, "err"); }
}

export async function twoFactor() {
  if (state.has2fa) {
    const password = field("Ton mot de passe", { type: "password", autocomplete: "current-password" });
    if (!await dialog({ title: "Désactiver la double authentification", text: "Ton compte sera protégé par le mot de passe seul.", body: password.el, ok: "Désactiver", danger: true })) return;
    try {
      await api("/api/account/2fa/disable", { method: "POST", form: { password: password.input.value } });
      state.has2fa = false;
      events.emit("account-changed");
      toast("Double authentification désactivée.", "ok");
    } catch (error) { toast(error.message, "err"); }
    return;
  }
  let data;
  try { data = await api("/api/account/2fa/start", { method: "POST" }); } catch (error) { toast(error.message, "err"); return; }
  const qr = h("div", { class: "qr" });
  try {
    const code = window.qrcode(0, "M");
    code.addData(data.uri);
    code.make();
    qr.innerHTML = code.createSvgTag({ cellSize: 5, margin: 1, scalable: true });
  } catch { qr.hidden = true; }
  const input = field("Code affiché par l'application", { inputmode: "numeric", maxlength: "6", placeholder: "000000", autocomplete: "one-time-code" });
  const body = h("div", {}, qr, h("p", { class: "secret" }, data.secret), input.el);
  if (!await dialog({ title: "Double authentification", text: "Scanne le code avec Aegis, Bitwarden ou Google Authenticator, puis saisis le code à 6 chiffres.", body, ok: "Activer" })) return;
  try {
    await api("/api/account/2fa/enable", { method: "POST", form: { code: input.input.value } });
    state.has2fa = true;
    events.emit("account-changed");
    toast("Double authentification activée.", "ok");
  } catch (error) { toast(error.message, "err"); }
}

export async function devices() {
  let list;
  try { list = await api("/api/account/sessions"); } catch (error) { toast(error.message, "err"); return; }
  const rows = h("div", { class: "list-rows" });
  for (const s of list) {
    const row = h("div", { class: "list-row" },
      I(/mobile|android|iphone/i.test(s.agent) ? "remote" : "monitor"),
      h("div", { class: "lr-main" },
        h("div", {}, s.current ? "Cet appareil" : "Autre appareil"),
        h("div", { class: "lr-sub" }, `${s.ip} · inactif depuis ${Math.floor(s.idle / 60)} min`),
        h("div", { class: "lr-sub" }, (s.agent || "").slice(0, 80))));
    if (!s.current) {
      row.append(h("button", { type: "button", class: "btn ghost small", onclick: async () => {
        try {
          await api("/api/account/sessions/revoke", { method: "POST", form: { id: s.id } });
          row.remove();
          toast("Appareil déconnecté.", "ok");
        } catch (error) { toast(error.message, "err"); }
      } }, "Déconnecter"));
    }
    rows.append(row);
  }
  dialog({ title: "Appareils connectés", body: rows, ok: false, cancel: "Fermer" });
}

export async function activity() {
  let list;
  try { list = await api("/api/account/audit"); } catch (error) { toast(error.message, "err"); return; }
  const rows = h("div", { class: "list-rows" }, list.map((r) => h("div", { class: "list-row" },
    h("div", { class: "lr-main" },
      h("div", {}, ACTIONS[r.action] || r.action),
      h("div", { class: "lr-sub" }, `${new Date(r.ts).toLocaleString("fr-FR")} · ${r.ip}`),
      r.detail ? h("div", { class: "lr-sub" }, r.detail.slice(0, 100)) : null))));
  if (!list.length) rows.append(h("div", { class: "muted" }, "Rien pour l'instant."));
  dialog({ title: "Activité récente", body: rows, ok: false, cancel: "Fermer" });
}
