/* SUSU —— 量化系统的卡通助手（参考用户形象：圆框眼镜/刘海/蓝白条纹衫）
   点击头像弹出随机金融名人名言。所有页面 include 本文件即可。 */
(function () {
  const QUOTES = [
    ["别人贪婪时我恐惧，别人恐惧时我贪婪。", "沃伦·巴菲特"],
    ["价格是你支付的，价值是你得到的。", "沃伦·巴菲特"],
    ["投资的第一条规则是永远不要亏钱；第二条是别忘了第一条。", "沃伦·巴菲特"],
    ["在别人恐惧时贪婪，需要的不是勇气，而是准备。", "沃伦·巴菲特"],
    ["反过来想，总是反过来想。", "查理·芒格"],
    ["要得到你想要的东西，最可靠的办法是让自己配得上它。", "查理·芒格"],
    ["市场短期是投票机，长期是称重机。", "本杰明·格雷厄姆"],
    ["聪明的投资者是现实主义者，向乐观者卖出，从悲观者手中买入。", "本杰明·格雷厄姆"],
    ["了解你持有的股票，以及你为什么持有它。", "彼得·林奇"],
    ["股市下跌就像科罗拉多一月的暴风雪一样平常，不必惊慌。", "彼得·林奇"],
    ["重要的不是你判断对错，而是对的时候赚多少、错的时候亏多少。", "乔治·索罗斯"],
    ["钱是坐着等出来的，不是靠频繁交易赚出来的。", "杰西·利弗莫尔"],
    ["痛苦 + 反思 = 进步。", "瑞·达利欧"],
    ["牛市在悲观中诞生，在怀疑中成长，在乐观中成熟，在亢奋中死亡。", "约翰·邓普顿"],
    ["市场保持非理性的时间，可能比你保持不破产的时间更长。", "凯恩斯"],
    ["风险来自你不知道自己在做什么。", "沃伦·巴菲特"],
  ];

  const AVATAR_SVG = `
  <svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <defs>
      <clipPath id="susuClip"><circle cx="50" cy="50" r="48"/></clipPath>
    </defs>
    <g clip-path="url(#susuClip)">
      <rect width="100" height="100" fill="#0f1117"/>
      <!-- 蓝白条纹衫 -->
      <rect x="10" y="80" width="80" height="20" fill="#eef5f0"/>
      <rect x="10" y="80" width="80" height="4" fill="#9cc7e8"/>
      <rect x="10" y="88" width="80" height="4" fill="#9cc7e8"/>
      <rect x="10" y="96" width="80" height="4" fill="#9cc7e8"/>
      <!-- 脖子 -->
      <rect x="42" y="70" width="16" height="16" fill="#f1c9a5"/>
      <!-- 头发后层 -->
      <ellipse cx="50" cy="44" rx="32" ry="34" fill="#2b2622"/>
      <!-- 脸 -->
      <ellipse cx="50" cy="48" rx="25" ry="27" fill="#f6d4b2"/>
      <!-- 刘海 -->
      <path d="M25 38 Q30 18 50 17 Q70 18 75 38 Q66 26 50 27 Q40 27 36 33 Q33 36 25 38Z" fill="#2b2622"/>
      <path d="M24 40 Q22 60 30 72 Q26 54 30 40Z" fill="#2b2622"/>
      <path d="M76 40 Q78 60 70 72 Q74 54 70 40Z" fill="#2b2622"/>
      <!-- 眉毛 -->
      <rect x="33" y="40" width="13" height="2.5" rx="1.2" fill="#6b5a4a"/>
      <rect x="54" y="40" width="13" height="2.5" rx="1.2" fill="#6b5a4a"/>
      <!-- 圆框眼镜 -->
      <rect x="31" y="44" width="17" height="14" rx="6" fill="#ffffff" fill-opacity="0.12" stroke="#3a3a3a" stroke-width="2"/>
      <rect x="52" y="44" width="17" height="14" rx="6" fill="#ffffff" fill-opacity="0.12" stroke="#3a3a3a" stroke-width="2"/>
      <line x1="48" y1="50" x2="52" y2="50" stroke="#3a3a3a" stroke-width="2"/>
      <!-- 眼睛 -->
      <circle cx="39.5" cy="51" r="2.6" fill="#1c1714"/>
      <circle cx="60.5" cy="51" r="2.6" fill="#1c1714"/>
      <!-- 腮红 -->
      <ellipse cx="34" cy="61" rx="4.5" ry="2.8" fill="#f4a9a0" fill-opacity="0.6"/>
      <ellipse cx="66" cy="61" rx="4.5" ry="2.8" fill="#f4a9a0" fill-opacity="0.6"/>
      <!-- 嘴 -->
      <path d="M45 65 Q50 69 55 65" stroke="#c47a6a" stroke-width="2" fill="none" stroke-linecap="round"/>
    </g>
  </svg>`;

  const css = `
  #susu-widget { position: fixed; right: 22px; bottom: 22px; z-index: 9999;
    display: flex; flex-direction: column; align-items: flex-end; gap: 10px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  #susu-bubble { max-width: 280px; background: #ffffff; border: 1px solid #ececec;
    border-radius: 12px; padding: 14px 16px; color: #18181b; font-size: 0.86rem;
    line-height: 1.6; box-shadow: 0 2px 12px rgba(0,0,0,0.06);
    opacity: 0; transform: translateY(6px); pointer-events: none;
    transition: opacity 0.22s ease, transform 0.22s ease; }
  #susu-bubble.show { opacity: 1; transform: translateY(0); pointer-events: auto; }
  #susu-bubble .q { color: #52525b; }
  #susu-bubble .a { color: #2563eb; font-size: 0.78rem; margin-top: 8px; text-align: right; }
  #susu-bubble::after { content: ""; position: absolute; right: 30px; bottom: -8px;
    width: 14px; height: 14px; background: #ffffff; border-right: 1px solid #ececec;
    border-bottom: 1px solid #ececec; transform: rotate(45deg); }
  #susu-btn { display: flex; align-items: center; gap: 8px; cursor: pointer;
    background: #ffffff; border: 1px solid #d4d4d8; border-radius: 40px;
    padding: 5px 14px 5px 5px; box-shadow: 0 2px 10px rgba(0,0,0,0.05);
    transition: border-color 0.18s ease; }
  #susu-btn:hover { border-color: #18181b; }
  #susu-btn svg { width: 44px; height: 44px; border-radius: 50%;
    border: 1px solid #ececec; flex-shrink: 0; }
  #susu-btn .name { font-weight: 600; font-size: 0.92rem; color: #18181b; }
  #susu-btn .role { display:block; font-size: 0.66rem; color: #9ca3af; font-weight: 400; }
  `;

  function init() {
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);

    const w = document.createElement("div");
    w.id = "susu-widget";
    w.innerHTML = `
      <div id="susu-bubble"></div>
      <div id="susu-btn" title="点我，听听投资大师怎么说">
        ${AVATAR_SVG}
        <div><span class="name">SUSU</span><span class="role">你的量化助手</span></div>
      </div>`;
    document.body.appendChild(w);

    const bubble = w.querySelector("#susu-bubble");
    const btn = w.querySelector("#susu-btn");
    let last = -1;

    function speak() {
      let i;
      do { i = Math.floor(Math.random() * QUOTES.length); } while (i === last && QUOTES.length > 1);
      last = i;
      const [q, a] = QUOTES[i];
      bubble.innerHTML = `<div class="q">“${q}”</div><div class="a">—— ${a}</div>`;
      bubble.classList.add("show");
    }
    btn.addEventListener("click", () => {
      if (bubble.classList.contains("show")) speak();   // 已开则换一句
      else speak();
    });
    document.addEventListener("click", e => {
      if (!w.contains(e.target)) bubble.classList.remove("show");
    });
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", init);
  else init();
})();
