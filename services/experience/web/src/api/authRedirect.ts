/** SPA login redirect — registered by the router so 401s avoid a full reload. */
type LoginRedirectHandler = () => void;

let loginRedirectHandler: LoginRedirectHandler | null = null;

export function registerLoginRedirect(handler: LoginRedirectHandler | null) {
  loginRedirectHandler = handler;
}

export function redirectToLogin() {
  if (loginRedirectHandler) {
    loginRedirectHandler();
    return;
  }
  // Before RouterProvider mounts (e.g. a 401 on the first fetch).
  if (!window.location.pathname.startsWith("/login")) {
    window.location.assign("/login");
  }
}
