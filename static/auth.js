/* 访问门禁 —— 对外开放时，首次访问要求输入口令。
   做两件事：
   1) 给所有 /api/ 请求自动带上已存口令头 X-Access-Password
   2) 任何 /api/ 请求若返回 401(需口令)，弹窗索要口令、存好并自动重试
   本地自用（服务端未设口令）时完全无感，不弹窗。
   所有页面应在其他脚本之前 include 本文件。 */
(function () {
  const KEY = "susu_pw";
  const origFetch = window.fetch.bind(window);

  function withPw(init, input) {
    init = Object.assign({}, init);
    const headers = new Headers(
      init.headers || (typeof input !== "string" && input && input.headers) || {}
    );
    const pw = localStorage.getItem(KEY);
    if (pw) headers.set("X-Access-Password", pw);
    init.headers = headers;
    return init;
  }

  // 弹窗校验口令直到正确；返回 true=已通过，false=用户放弃
  async function askPassword() {
    while (true) {
      const pw = window.prompt(
        "🔒 本服务已开启访问保护\n请输入主人分享给你的访问口令：");
      if (pw === null) return false;
      let ok = false;
      try {
        ok = (await (await origFetch(
          "/api/auth_status?pw=" + encodeURIComponent(pw))).json()).ok;
      } catch (e) { return false; }
      if (ok) { localStorage.setItem(KEY, pw); return true; }
      window.alert("口令不对，再试一次～");
    }
  }

  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    if (url.indexOf("/api/") === -1) return origFetch(input, init);

    let res = await origFetch(input, withPw(init, input));
    if (res.status === 401) {
      let needAuth = false;
      try { needAuth = (await res.clone().json()).auth_required; } catch (e) {}
      if (needAuth && await askPassword())
        res = await origFetch(input, withPw(init, input));   // 带新口令重试
    }
    return res;
  };
})();
