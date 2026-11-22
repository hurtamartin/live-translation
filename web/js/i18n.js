// Interface strings for listeners. The listener's phone language decides the UI
// language; language names come from Intl.DisplayNames so every endonym is right.

const STR = {
  en: {
    live: 'Live', offAir: 'Off air', connecting: 'Connecting…',
    reconnecting: 'Connection lost. Reconnecting…',
    chooseLang: 'Choose your language', chooseHint: 'You will read and hear the translation in this language.',
    changeLang: 'Tap to change language', noLangs: 'No languages are available yet.',
    offTitle: 'The broadcast has not started', offBody: 'Keep this page open. The translation appears here as soon as the speaker begins.',
    waitTitle: 'Waiting for the speaker', waitBody: 'The translation appears here as it is spoken.',
    startTitle: 'Starting the translation', startBody: 'This takes a moment.',
    errTitle: 'The translation is unavailable right now', errBody: 'It resumes automatically. Keep this page open.',
    listen: 'Listen', stop: 'Stop listening', loading: 'Loading audio…', playing: 'Playing',
    tapListen: 'Tap play to listen', noAudio: 'Audio is not available on this device',
    settings: 'Settings', textSize: 'Text size', showSrc: 'Show the original text',
    bg: 'Keep playing with the screen locked', bgHint: 'Audio arrives a little later, but keeps playing in your pocket.',
    awake: 'Keep the screen on', theme: 'Appearance', dark: 'Dark', light: 'Light', auto: 'System',
    share: 'Invite others', shareHint: 'People next to you can scan this code.', shareBtn: 'Share link',
    install: 'Install the app', installIos: 'In Safari, tap Share and then Add to Home Screen.',
    jump: 'Jump to latest', close: 'Close', unavailable: 'This language is no longer available.',
    resume: 'Tap play to resume audio',
    copyLink: 'Copy link', copied: 'Link copied',
  },
  cs: {
    live: 'Živě', offAir: 'Nevysílá se', connecting: 'Připojuji…',
    reconnecting: 'Spojení přerušeno. Obnovuji…',
    chooseLang: 'Vyberte jazyk', chooseHint: 'V tomto jazyce budete překlad číst i slyšet.',
    changeLang: 'Klepnutím změníte jazyk', noLangs: 'Zatím není k dispozici žádný jazyk.',
    offTitle: 'Vysílání ještě nezačalo', offBody: 'Nechte stránku otevřenou. Překlad se tu objeví, jakmile řečník začne.',
    waitTitle: 'Čekáme na řečníka', waitBody: 'Překlad se tu objevuje průběžně, jak zazní.',
    startTitle: 'Spouštím překlad', startBody: 'Chvilku to potrvá.',
    errTitle: 'Překlad je teď nedostupný', errBody: 'Obnoví se automaticky. Nechte stránku otevřenou.',
    listen: 'Poslouchat', stop: 'Přestat poslouchat', loading: 'Načítám zvuk…', playing: 'Hraje',
    tapListen: 'Pro poslech klepněte na přehrát', noAudio: 'Zvuk na tomto zařízení není dostupný',
    settings: 'Nastavení', textSize: 'Velikost písma', showSrc: 'Zobrazit původní text',
    bg: 'Hrát i se zamčenou obrazovkou', bgHint: 'Zvuk přijde o něco později, ale hraje dál i v kapse.',
    awake: 'Nezhasínat displej', theme: 'Vzhled', dark: 'Tmavý', light: 'Světlý', auto: 'Systém',
    share: 'Pozvat další', shareHint: 'Lidé vedle vás si mohou naskenovat tento kód.', shareBtn: 'Sdílet odkaz',
    install: 'Nainstalovat aplikaci', installIos: 'V Safari klepněte na Sdílet a pak na Přidat na plochu.',
    jump: 'Na nejnovější', close: 'Zavřít', unavailable: 'Tento jazyk už není k dispozici.',
    resume: 'Klepnutím na přehrát obnovíte zvuk',
    copyLink: 'Kopírovat odkaz', copied: 'Odkaz zkopírován',
  },
  de: {
    live: 'Live', offAir: 'Keine Übertragung', connecting: 'Verbinde…',
    reconnecting: 'Verbindung unterbrochen. Verbinde neu…',
    chooseLang: 'Sprache wählen', chooseHint: 'In dieser Sprache lesen und hören Sie die Übersetzung.',
    changeLang: 'Tippen, um die Sprache zu ändern', noLangs: 'Noch ist keine Sprache verfügbar.',
    offTitle: 'Die Übertragung hat noch nicht begonnen', offBody: 'Lassen Sie diese Seite geöffnet. Die Übersetzung erscheint hier, sobald der Vortrag beginnt.',
    waitTitle: 'Warten auf den Vortrag', waitBody: 'Die Übersetzung erscheint hier fortlaufend.',
    startTitle: 'Übersetzung wird gestartet', startBody: 'Das dauert einen Moment.',
    errTitle: 'Die Übersetzung ist gerade nicht verfügbar', errBody: 'Sie wird automatisch fortgesetzt. Lassen Sie die Seite geöffnet.',
    listen: 'Anhören', stop: 'Wiedergabe beenden', loading: 'Audio wird geladen…', playing: 'Läuft',
    tapListen: 'Zum Anhören auf Play tippen', noAudio: 'Audio ist auf diesem Gerät nicht verfügbar',
    settings: 'Einstellungen', textSize: 'Schriftgröße', showSrc: 'Originaltext anzeigen',
    bg: 'Bei gesperrtem Bildschirm weiterspielen', bgHint: 'Der Ton kommt etwas später, spielt aber auch in der Tasche weiter.',
    awake: 'Bildschirm anlassen', theme: 'Darstellung', dark: 'Dunkel', light: 'Hell', auto: 'System',
    share: 'Andere einladen', shareHint: 'Personen neben Ihnen können diesen Code scannen.', shareBtn: 'Link teilen',
    install: 'App installieren', installIos: 'Tippen Sie in Safari auf Teilen und dann auf Zum Home-Bildschirm.',
    jump: 'Zum Neuesten', close: 'Schließen', unavailable: 'Diese Sprache ist nicht mehr verfügbar.',
    resume: 'Zum Fortsetzen auf Play tippen',
    copyLink: 'Link kopieren', copied: 'Link kopiert',
  },
  uk: {
    live: 'Наживо', offAir: 'Трансляції немає', connecting: 'Підключення…',
    reconnecting: 'Зʼєднання втрачено. Відновлюємо…',
    chooseLang: 'Оберіть мову', chooseHint: 'Цією мовою ви читатимете й слухатимете переклад.',
    changeLang: 'Торкніться, щоб змінити мову', noLangs: 'Поки що немає доступних мов.',
    offTitle: 'Трансляція ще не почалася', offBody: 'Не закривайте сторінку. Переклад зʼявиться тут, щойно промовець почне.',
    waitTitle: 'Чекаємо на промовця', waitBody: 'Переклад зʼявляється тут у міру мовлення.',
    startTitle: 'Запускаємо переклад', startBody: 'Це займе мить.',
    errTitle: 'Переклад зараз недоступний', errBody: 'Він відновиться автоматично. Не закривайте сторінку.',
    listen: 'Слухати', stop: 'Припинити', loading: 'Завантаження звуку…', playing: 'Відтворення',
    tapListen: 'Натисніть «відтворити», щоб слухати', noAudio: 'Звук недоступний на цьому пристрої',
    settings: 'Налаштування', textSize: 'Розмір тексту', showSrc: 'Показувати оригінал',
    bg: 'Грати із заблокованим екраном', bgHint: 'Звук приходить трохи пізніше, але грає навіть у кишені.',
    awake: 'Не вимикати екран', theme: 'Вигляд', dark: 'Темний', light: 'Світлий', auto: 'Система',
    share: 'Запросити інших', shareHint: 'Люди поруч можуть відсканувати цей код.', shareBtn: 'Поділитися посиланням',
    install: 'Встановити застосунок', installIos: 'У Safari натисніть «Поділитися», а потім «На початковий екран».',
    jump: 'До найновішого', close: 'Закрити', unavailable: 'Ця мова більше недоступна.',
    resume: 'Натисніть «відтворити», щоб продовжити',
    copyLink: 'Копіювати посилання', copied: 'Посилання скопійовано',
  },
  pl: {
    live: 'Na żywo', offAir: 'Brak transmisji', connecting: 'Łączenie…',
    reconnecting: 'Utracono połączenie. Łączę ponownie…',
    chooseLang: 'Wybierz język', chooseHint: 'W tym języku będziesz czytać i słyszeć tłumaczenie.',
    changeLang: 'Dotknij, aby zmienić język', noLangs: 'Nie ma jeszcze dostępnych języków.',
    offTitle: 'Transmisja jeszcze się nie zaczęła', offBody: 'Nie zamykaj strony. Tłumaczenie pojawi się tutaj, gdy mówca zacznie.',
    waitTitle: 'Czekamy na mówcę', waitBody: 'Tłumaczenie pojawia się tutaj na bieżąco.',
    startTitle: 'Uruchamiam tłumaczenie', startBody: 'To potrwa chwilę.',
    errTitle: 'Tłumaczenie jest teraz niedostępne', errBody: 'Wznowi się automatycznie. Nie zamykaj strony.',
    listen: 'Słuchaj', stop: 'Przestań słuchać', loading: 'Wczytywanie dźwięku…', playing: 'Odtwarzanie',
    tapListen: 'Dotknij odtwórz, aby słuchać', noAudio: 'Dźwięk nie jest dostępny na tym urządzeniu',
    settings: 'Ustawienia', textSize: 'Rozmiar tekstu', showSrc: 'Pokaż tekst oryginalny',
    bg: 'Odtwarzaj przy zablokowanym ekranie', bgHint: 'Dźwięk dociera nieco później, ale gra nawet w kieszeni.',
    awake: 'Nie wygaszaj ekranu', theme: 'Wygląd', dark: 'Ciemny', light: 'Jasny', auto: 'System',
    share: 'Zaproś innych', shareHint: 'Osoby obok mogą zeskanować ten kod.', shareBtn: 'Udostępnij link',
    install: 'Zainstaluj aplikację', installIos: 'W Safari dotknij Udostępnij, a potem Do ekranu początkowego.',
    jump: 'Do najnowszego', close: 'Zamknij', unavailable: 'Ten język nie jest już dostępny.',
    resume: 'Dotknij odtwórz, aby wznowić dźwięk',
    copyLink: 'Kopiuj link', copied: 'Skopiowano link',
  },
  es: {
    live: 'En directo', offAir: 'Sin emisión', connecting: 'Conectando…',
    reconnecting: 'Se perdió la conexión. Reconectando…',
    chooseLang: 'Elige tu idioma', chooseHint: 'Leerás y escucharás la traducción en este idioma.',
    changeLang: 'Toca para cambiar de idioma', noLangs: 'Todavía no hay idiomas disponibles.',
    offTitle: 'La emisión aún no ha empezado', offBody: 'Deja esta página abierta. La traducción aparecerá aquí en cuanto empiece el orador.',
    waitTitle: 'Esperando al orador', waitBody: 'La traducción aparece aquí a medida que se habla.',
    startTitle: 'Iniciando la traducción', startBody: 'Tardará un momento.',
    errTitle: 'La traducción no está disponible ahora', errBody: 'Se reanudará automáticamente. Deja la página abierta.',
    listen: 'Escuchar', stop: 'Dejar de escuchar', loading: 'Cargando audio…', playing: 'Reproduciendo',
    tapListen: 'Toca reproducir para escuchar', noAudio: 'El audio no está disponible en este dispositivo',
    settings: 'Ajustes', textSize: 'Tamaño del texto', showSrc: 'Mostrar el texto original',
    bg: 'Seguir sonando con la pantalla bloqueada', bgHint: 'El audio llega un poco más tarde, pero sigue sonando en el bolsillo.',
    awake: 'Mantener la pantalla encendida', theme: 'Apariencia', dark: 'Oscuro', light: 'Claro', auto: 'Sistema',
    share: 'Invitar a otros', shareHint: 'Las personas a tu lado pueden escanear este código.', shareBtn: 'Compartir enlace',
    install: 'Instalar la app', installIos: 'En Safari, toca Compartir y luego Añadir a pantalla de inicio.',
    jump: 'Ir a lo último', close: 'Cerrar', unavailable: 'Este idioma ya no está disponible.',
    resume: 'Toca reproducir para reanudar el audio',
    copyLink: 'Copiar enlace', copied: 'Enlace copiado',
  },
  fr: {
    live: 'En direct', offAir: 'Pas de diffusion', connecting: 'Connexion…',
    reconnecting: 'Connexion perdue. Reconnexion…',
    chooseLang: 'Choisissez votre langue', chooseHint: 'Vous lirez et entendrez la traduction dans cette langue.',
    changeLang: 'Touchez pour changer de langue', noLangs: 'Aucune langue n’est encore disponible.',
    offTitle: 'La diffusion n’a pas encore commencé', offBody: 'Gardez cette page ouverte. La traduction apparaîtra ici dès que l’orateur commencera.',
    waitTitle: 'En attente de l’orateur', waitBody: 'La traduction apparaît ici au fil de la parole.',
    startTitle: 'Démarrage de la traduction', startBody: 'Cela prend un instant.',
    errTitle: 'La traduction est indisponible pour le moment', errBody: 'Elle reprendra automatiquement. Gardez la page ouverte.',
    listen: 'Écouter', stop: 'Arrêter l’écoute', loading: 'Chargement de l’audio…', playing: 'Lecture',
    tapListen: 'Touchez lecture pour écouter', noAudio: 'L’audio n’est pas disponible sur cet appareil',
    settings: 'Réglages', textSize: 'Taille du texte', showSrc: 'Afficher le texte original',
    bg: 'Continuer avec l’écran verrouillé', bgHint: 'Le son arrive un peu plus tard, mais continue dans la poche.',
    awake: 'Garder l’écran allumé', theme: 'Apparence', dark: 'Sombre', light: 'Clair', auto: 'Système',
    share: 'Inviter d’autres personnes', shareHint: 'Vos voisins peuvent scanner ce code.', shareBtn: 'Partager le lien',
    install: 'Installer l’app', installIos: 'Dans Safari, touchez Partager puis Sur l’écran d’accueil.',
    jump: 'Aller au plus récent', close: 'Fermer', unavailable: 'Cette langue n’est plus disponible.',
    resume: 'Touchez lecture pour reprendre l’audio',
    copyLink: 'Copier le lien', copied: 'Lien copié',
  },
};

function pickUiLang() {
  for (const l of navigator.languages || [navigator.language || 'en']) {
    const base = String(l).toLowerCase().split('-')[0];
    if (STR[base]) return base;
  }
  return 'en';
}

export const uiLang = pickUiLang();
document.documentElement.lang = uiLang;

export function t(key) {
  return (STR[uiLang] && STR[uiLang][key]) || STR.en[key] || key;
}

const cache = new Map();
function display(locale, code) {
  const k = locale + '|' + code;
  if (cache.has(k)) return cache.get(k);
  let name = '';
  try { name = new Intl.DisplayNames([locale], { type: 'language' }).of(code) || ''; } catch { /* old browser */ }
  if (name && name !== code) name = name.charAt(0).toLocaleUpperCase(locale) + name.slice(1);
  cache.set(k, name);
  return name;
}

/** Name of the language in that language, e.g. "Deutsch". */
export function nativeName(code) {
  return display(code, code) || display('en', code) || code;
}

/** Name in the listener's UI language, e.g. "Němčina" for a Czech phone. */
export function localName(code) {
  return display(uiLang, code) || code;
}

/** Code split for the channel readout: "pt-BR" -> ["PT", "BR"]. */
export function codeParts(code) {
  const [main, sub] = String(code).split('-');
  return [main.toUpperCase(), sub ? sub.toUpperCase().slice(0, 4) : ''];
}
