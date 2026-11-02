(() => {
  const settings = /*__SETTINGS__*/;
  if (!/^https?:$/.test(location.protocol) || (settings.noframes && window !== top)) return;
  const url = location.href.split('#')[0];
  const matches = patterns => patterns.some(pattern => new RegExp(pattern).test(url));
  if ((settings.matches.length || settings.includes.length) &&
      !matches(settings.matches) && !matches(settings.includes)) return;
  if (matches(settings.excludes)) return;
  const check = new Event(settings.guard, { cancelable: true });
  if (!window.dispatchEvent(check)) return;
  window.addEventListener(settings.guard, event => event.preventDefault());
  const privateFactory = /*__PRIVACY_RUNTIME__*/null;
  const profilePrivacy = /*__PROFILE_PRIVACY__*/null;
  const preparation = settings.privateState ? privateFactory(profilePrivacy &&
    { ...profilePrivacy, client: settings.guard }) : null;
  if (preparation) preparation.catch(() => {});
  const execute = privacy => {
    try {
      (function () {
/*__SOURCE__*/
      }).call(window);
    } catch (error) {
      console.error(error);
    }
  };
  const run = () => {
    if (preparation) preparation.then(execute).catch(error => console.error(error));
    else execute(null);
  };
  if (settings.runAt === 'document-start') run();
  else if (settings.runAt === 'document-end') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run, { once: true });
    else run();
  } else {
    const idle = () => setTimeout(run, 0);
    if (document.readyState !== 'loading') idle();
    else document.addEventListener('DOMContentLoaded', idle, { once: true });
  }
})();
