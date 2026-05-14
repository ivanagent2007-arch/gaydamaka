(function () {
  var parts = location.pathname.split("/").filter(Boolean);
  var page = parts.length ? parts[parts.length - 1] : "index.html";
  if (!page || page === "/") page = "index.html";

  var mainTabs = [
    { href: "index.html", match: ["index.html", ""], label: "Расписание" },
    { href: "deadlines.html", match: ["deadlines.html"], label: "Дедлайны" },
    { href: "profile.html", match: ["profile.html"], label: "Баллы" },
  ];

  var moreTabs = [
    { href: "attendance.html", match: ["attendance.html"], label: "Посещаемость" },
    { href: "birthdays.html", match: ["birthdays.html"], label: "Дни рождения" },
    { href: "mail.html", match: ["mail.html"], label: "Почта группы" },
    { href: "group_members.html", match: ["group_members.html"], label: "Состав" },
    { href: "santa.html", match: ["santa.html"], label: "Санта" },
  ];

  var inMore = moreTabs.some(function (t) {
    return t.match.indexOf(page) >= 0;
  });

  function isActive(tab) {
    return tab.match.indexOf(page) >= 0;
  }

  function haptic() {
    var tgw = window.Telegram && window.Telegram.WebApp;
    if (tgw && tgw.HapticFeedback && tgw.HapticFeedback.impactOccurred) {
      try { tgw.HapticFeedback.impactOccurred("light"); } catch (e) {}
    }
  }

  var nav = document.createElement("nav");
  nav.className = "app-tabbar";
  nav.setAttribute("aria-label", "Разделы");

  mainTabs.forEach(function (tab) {
    var a = document.createElement("a");
    a.href = tab.href;
    a.className = "app-tab" + (isActive(tab) ? " active" : "");
    a.textContent = tab.label;
    a.addEventListener("click", haptic);
    nav.appendChild(a);
  });

  var moreBtn = document.createElement("button");
  moreBtn.type = "button";
  moreBtn.className = "app-tab app-tab-more" + (inMore ? " active" : "");
  moreBtn.textContent = "Ещё";
  moreBtn.setAttribute("aria-haspopup", "true");
  moreBtn.setAttribute("aria-expanded", "false");

  var popup = document.createElement("div");
  popup.className = "more-popup";
  popup.hidden = true;

  moreTabs.forEach(function (tab) {
    var a = document.createElement("a");
    a.href = tab.href;
    a.className = "more-item" + (isActive(tab) ? " active" : "");
    a.textContent = tab.label;
    a.addEventListener("click", haptic);
    popup.appendChild(a);
  });

  moreBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    haptic();
    var show = popup.hidden;
    popup.hidden = !show;
    moreBtn.setAttribute("aria-expanded", show ? "true" : "false");
  });

  document.addEventListener("click", function () {
    popup.hidden = true;
    moreBtn.setAttribute("aria-expanded", "false");
  });

  nav.appendChild(moreBtn);
  document.body.appendChild(popup);
  document.body.appendChild(nav);
})();
