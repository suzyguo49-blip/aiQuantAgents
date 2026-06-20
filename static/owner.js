/* 管理员门禁 —— 主人专用页面(/admin、/today)用。
   给 /api/admin/* 和 /api/portfolio 请求带上 X-Admin-Key；
   遇 403(需管理员密钥)则弹窗索要、存好并自动重试。
   与 auth.js 叠加工作:auth.js 管访问口令，owner.js 管管理员密钥。
   须在 auth.js 之后 include。 */
(function () {
  const KEY = "susu_admin_key";
  const origFetch = window.fetch.bind(window);   // 此时已是 auth.js 包装过的 fetch

  // 对所有 /api/ 请求都附带管理员密钥（非受保护接口会忽略它，无害），
  // 并在 403 时弹窗——这样新增的管理员接口不会再漏。
  function isOwnerApi(url) {
    return url.indexOf("/api/") !== -1;
  }

  function withKey(init, input) {
    init = Object.assign({}, init);
    const headers = new Headers(
      init.headers || (typeof input !== "string" && input && input.headers) || {}
    );
    const k = localStorage.getItem(KEY);
    if (k) headers.set("X-Admin-Key", k);
    init.headers = headers;
    return init;
  }

  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    if (!isOwnerApi(url)) return origFetch(input, init);

    let res = await origFetch(input, withKey(init, input));
    while (res.status === 403) {
      let needKey = false;
      try { needKey = (await res.clone().json()).admin_required; } catch (e) {}
      if (!needKey) break;
      const k = window.prompt("🔑 主人专用功能\n请输入管理员密钥：");
      if (k === null) break;                       // 用户取消
      localStorage.setItem(KEY, k);
      res = await origFetch(input, withKey(init, input));   // 带新密钥重试
    }
    return res;
  };
})();
