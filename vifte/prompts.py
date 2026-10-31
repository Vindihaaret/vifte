"""Promptforslag brukeren kan lime inn i Claude. Hele teksten finnes på hvert språk, derfor ikke i _()."""
from .i18n import current

SETUP = {
    "nb": """\
Jeg er ny bruker av Vifte og vil sette opp viftestyringen for første gang, og teste den med Stress.

Før du endrer noe:
1. Sjekk at du har tilgang til både Vifte og Stress. Hvis noe mangler, fortell meg hva jeg må installere eller slå på, og vent til jeg har gjort det.
2. Spør meg hva jeg bruker PC-en til, og hvordan maskinen skal oppføre seg ved vanlig bruk og ved tung belastning. Spør også hvor lenge testen totalt skal vare. Vent på svaret. Still bare de spørsmålene du trenger, men spør selv om du tror du kjenner svaret fra tidligere samtaler.
3. Spør om Vifte også skal styre viftene på skjermkortet, eller om skjermkortet skal styre dem selv. Forklar at viftene må gå hele tiden, minst på Viftes laveste hastighet, hvis Vifte styrer dem. Da virker ikke lenger skjermkortets stopp av viftene ved lett bruk. Vent på svaret.
4. Sjekk hvilke konfigurasjoner som finnes. Hvis "Claude" ikke finnes, spør om du skal opprette den for meg, og vent på svaret. Fortell om den blir en kopi av den aktive konfigurasjonen, og tøm den for kurver og viftestyring hvis jeg vil starte på nytt. Hvis "Claude" finnes, bytt til den. Bekreft at "Claude" er aktiv før du går videre. Ikke endre andre konfigurasjoner.
5. Finn ut hvilke sensorer og vifter som er tilkoblet, og fortell kort hva du fant. Spør meg hva hver vifte er og hvor den sitter, hvis du ikke kan se det sikkert. Ikke gjett ut fra navn, andre konfigurasjoner eller tidligere samtaler. Spør også om maskinen har vannkjøling eller en pumpe.

Dette skal du ikke røre:
- Pumper og alt som er låst.
- Viftene på skjermkortet, hvis jeg har valgt at skjermkortet skal styre dem. Bruk da GPU-temperaturen bare som input til kurvene for de andre viftene.

Oppsett:
6. Hvis maskinen har en pumpe, skal den være merket som pumpe i Vifte før paringen. Du kan ikke merke den selv. Hvis den ikke er merket, forklar hvordan jeg gjør det, og ikke start paringen før du har sjekket at den er merket. Hvis du er usikker på om et uttak er en pumpe, stopp og spør meg.
7. Si fra før paringen starter. Alle uttak testes, også skjermkortviftene, så det kan bli høylytt, og det kan ta flere minutter. Paringen endrer ikke hvem som styrer skjermkortviftene.
8. Kalibrer bare viftene som skal styres, og gi dem tydelige navn.
9. Lag kurver som passer bruken min, på de mest relevante temperaturene, og koble dem til viftene.

Test og justering:
10. Si fra før du starter hver test, siden viftene kan bli høylytte. Be meg også lukke tunge programmer og la maskinen stå i ro under testen.
11. Lag en testprofil i faser som gjenspeiler bruken min, med egen CPU- og GPU-belastning for hver type bruk. Begynn med oppvarming på lett bruk, for eksempel kontor og lett foto/video. Øk deretter gradvis til de tyngste oppgavene mine, og avslutt med en rolig fase. Vis meg profilen før du starter. Du skal vurdere dette selv ut fra svarene mine, ikke bruke én jevn belastning.
12. Del testtiden jeg oppga likt på to runder med samme profil. Hvis tiden er for kort til at fasene gir nyttige målinger, si fra før du starter.
13. Paring og kalibrering kan gjøre maskinen kaldere eller varmere enn normalt. Vent derfor til CPU og GPU er stabile på vanlig tomgang før første runde.
14. Kjør første runde. Les av temperaturer og viftehastighet i hver fase, og gi et anslag av støyen ut fra RPM. Se særlig på hvor stille det er i rolige faser, hvor raskt viftene reagerer, og om de går opp og ned.
15. Juster kurvene. Vent til CPU og GPU er tilbake på omtrent samme temperatur som ved starten av første runde, og kjør andre runde for å sammenligne. Si fra hvis temperaturene ikke kom helt ned.
16. Paring, kalibrering, venting før første runde og nedkjøling teller ikke med i testtiden.
17. Hvis en justering ikke hjalp, gå tilbake til den beste målte versjonen, og si tydelig fra at den ikke er målt på nytt etter tilbakestillingen. Ikke overskrid testtiden for å måle den igjen.

Til slutt:
18. Vis før/etter-resultatene i en kort tabell og forklar hva du endret og hvorfor, på en måte en nybegynner forstår.
19. Si tydelig fra hvis temperaturene ikke rakk å flate ut, og at testen da viser hvordan viftene reagerer, men ikke hvor varm maskinen blir over lang tid. Foreslå en lengre test hvis det trengs.
20. Spør meg før du lagrer konfigurasjonen. Si fra hvis noe fortsatt ikke er lagret.

Hvis noe går galt eller virker usikkert, stopp og si fra.
""",
    "en": """\
I am a new Vifte user and want to set up fan control for the first time, and test it with Stress.

Before you change anything:
1. Check that you have access to both Vifte and Stress. If something is missing, tell me what I need to install or turn on, and wait until I have done it.
2. Ask me what I use the PC for, and how the machine should behave in normal use and under heavy load. Also ask how long the test should last in total. Wait for my answer. Ask only the questions you need, but do ask even if you think you know the answer from earlier conversations.
3. Ask whether Vifte should also control the fans on the graphics card, or whether the graphics card should control them itself. Explain that if Vifte controls them, the fans must run all the time, at least at Vifte's lowest speed. The graphics card's fan stop at light use then no longer works. Wait for my answer.
4. Check which configurations exist. If "Claude" does not exist, ask whether you should create it for me, and wait for my answer. Tell me whether it will be a copy of the active configuration, and clear it of curves and fan control if I want to start over. If "Claude" exists, switch to it. Confirm that "Claude" is active before you continue. Do not change other configurations.
5. Find out which sensors and fans are connected, and briefly tell me what you found. Ask me what each fan is and where it sits if you can't tell for certain. Don't guess from names, other configurations or earlier conversations. Also ask whether the machine has water cooling or a pump.

What you must not touch:
- Pumps and anything that is locked.
- The fans on the graphics card, if I have chosen that the graphics card should control them. In that case, use the GPU temperature only as input to the curves for the other fans.

Setup:
6. If the machine has a pump, it must be marked as a pump in Vifte before pairing. You can't mark it yourself. If it isn't marked, explain how I do it, and don't start pairing until you have checked that it is marked. If you are unsure whether an output is a pump, stop and ask me.
7. Tell me before pairing starts. All outputs are tested, including the graphics card fans, so it can get loud and may take several minutes. Pairing does not change who controls the graphics card fans.
8. Calibrate only the fans that will be controlled, and give them clear names.
9. Create curves that suit my use, on the most relevant temperatures, and connect them to the fans.

Testing and tuning:
10. Tell me before you start each test, since the fans can get loud. Also ask me to close heavy programs and leave the machine alone during the test.
11. Create a test profile in phases that reflects my use, with separate CPU and GPU load for each type of use. Start with a warm-up at light use, such as office work and light photo/video. Then increase gradually to my heaviest tasks, and finish with a calm phase. Show me the profile before you start. You should judge this yourself from my answers, not use one steady load.
12. Divide the test time I gave equally between two rounds with the same profile. If the time is too short for the phases to give useful measurements, tell me before you start.
13. Pairing and calibration can leave the machine colder or warmer than normal. So wait until the CPU and GPU are stable at normal idle before the first round.
14. Run the first round. Read temperatures and fan speed in each phase, and estimate the noise from the RPM. Pay particular attention to how quiet it is in calm phases, how quickly the fans react, and whether they hunt up and down.
15. Adjust the curves. Wait until the CPU and GPU are back at about the same temperature as at the start of the first round, and run the second round to compare. Tell me if the temperatures did not come all the way down.
16. Pairing, calibration, waiting before the first round and cooling down do not count towards the test time.
17. If an adjustment didn't help, go back to the best measured version, and state clearly that it has not been measured again after the rollback. Don't exceed the test time to measure it again.

Finally:
18. Show the before/after results in a short table and explain what you changed and why, in a way a beginner understands.
19. State clearly if the temperatures did not have time to level off, and that the test then shows how the fans react, but not how warm the machine gets over a long time. Suggest a longer test if needed.
20. Ask me before you save the configuration. Tell me if anything is still unsaved.

If something goes wrong or seems uncertain, stop and tell me.
""",
    "de": """\
Ich bin neu bei Vifte und möchte die Lüftersteuerung zum ersten Mal einrichten und mit Stress testen.

Bevor du etwas änderst:
1. Prüfe, ob du Zugriff auf Vifte und Stress hast. Wenn etwas fehlt, sag mir, was ich installieren oder einschalten muss, und warte, bis ich es getan habe.
2. Frag mich, wofür ich den PC verwende und wie sich das System bei normaler Nutzung und unter hoher Last verhalten soll. Frag auch, wie lange der Test insgesamt dauern soll. Warte auf meine Antwort. Stelle nur die Fragen, die du brauchst, aber frag auch dann, wenn du die Antwort aus früheren Gesprächen zu kennen glaubst.
3. Frag, ob Vifte auch die Lüfter der Grafikkarte steuern soll oder ob die Grafikkarte sie selbst steuern soll. Erkläre, dass die Lüfter immer laufen müssen, mindestens mit Viftes niedrigster Drehzahl, wenn Vifte sie steuert. Der Lüfterstopp der Grafikkarte bei leichter Nutzung funktioniert dann nicht mehr. Warte auf meine Antwort.
4. Prüfe, welche Konfigurationen es gibt. Wenn es „Claude“ nicht gibt, frag, ob du sie für mich anlegen sollst, und warte auf meine Antwort. Sag mir, ob sie eine Kopie der aktiven Konfiguration wird, und entferne Kurven und Lüftersteuerung daraus, wenn ich neu anfangen möchte. Wenn es „Claude“ gibt, wechsle dorthin. Bestätige, dass „Claude“ aktiv ist, bevor du weitermachst. Ändere keine anderen Konfigurationen.
5. Finde heraus, welche Sensoren und Lüfter angeschlossen sind, und sag mir kurz, was du gefunden hast. Frag mich, was jeder Lüfter ist und wo er sitzt, wenn du es nicht sicher erkennen kannst. Rate nicht anhand von Namen, anderen Konfigurationen oder früheren Gesprächen. Frag auch, ob das System eine Wasserkühlung oder eine Pumpe hat.

Das darfst du nicht anfassen:
- Pumpen und alles, was gesperrt ist.
- Die Lüfter der Grafikkarte, wenn ich gewählt habe, dass die Grafikkarte sie steuert. Verwende die GPU-Temperatur dann nur als Eingabe für die Kurven der anderen Lüfter.

Einrichtung:
6. Wenn das System eine Pumpe hat, muss sie vor dem Pairing in Vifte als Pumpe markiert sein. Du kannst sie nicht selbst markieren. Wenn sie nicht markiert ist, erkläre mir, wie das geht, und starte das Pairing erst, wenn du geprüft hast, dass sie markiert ist. Wenn du unsicher bist, ob ein Ausgang eine Pumpe ist, halte an und frag mich.
7. Sag Bescheid, bevor das Pairing beginnt. Alle Ausgänge werden getestet, auch die Grafikkartenlüfter, daher kann es laut werden und mehrere Minuten dauern. Das Pairing ändert nicht, wer die Grafikkartenlüfter steuert.
8. Kalibriere nur die Lüfter, die gesteuert werden sollen, und gib ihnen eindeutige Namen.
9. Erstelle Kurven, die zu meiner Nutzung passen, auf den relevantesten Temperaturen, und verbinde sie mit den Lüftern.

Test und Feinabstimmung:
10. Sag Bescheid, bevor du jeden Test startest, da die Lüfter laut werden können. Bitte mich auch, schwere Programme zu schließen und das System während des Tests in Ruhe zu lassen.
11. Erstelle ein Testprofil in Phasen, das meine Nutzung widerspiegelt, mit eigener CPU- und GPU-Last für jede Nutzungsart. Beginne mit dem Aufwärmen bei leichter Nutzung, zum Beispiel Büroarbeit und leichter Foto-/Videobearbeitung. Steigere dann schrittweise bis zu meinen schwersten Aufgaben und ende mit einer ruhigen Phase. Zeig mir das Profil, bevor du startest. Beurteile das selbst anhand meiner Antworten und verwende keine gleichmäßige Last.
12. Teile die von mir genannte Testzeit gleichmäßig auf zwei Runden mit demselben Profil auf. Wenn die Zeit zu kurz ist, damit die Phasen brauchbare Messwerte liefern, sag es, bevor du startest.
13. Pairing und Kalibrierung können das System kälter oder wärmer als normal machen. Warte deshalb vor der ersten Runde, bis CPU und GPU im normalen Leerlauf stabil sind.
14. Führe die erste Runde aus. Lies in jeder Phase Temperaturen und Lüfterdrehzahl ab und schätze die Lautstärke anhand der RPM. Achte besonders darauf, wie leise es in ruhigen Phasen ist, wie schnell die Lüfter reagieren und ob sie auf und ab pendeln.
15. Passe die Kurven an. Warte, bis CPU und GPU wieder ungefähr die Temperatur vom Beginn der ersten Runde haben, und führe die zweite Runde zum Vergleich aus. Sag Bescheid, wenn die Temperaturen nicht ganz heruntergekommen sind.
16. Pairing, Kalibrierung, das Warten vor der ersten Runde und das Abkühlen zählen nicht zur Testzeit.
17. Wenn eine Anpassung nicht geholfen hat, geh zur besten gemessenen Version zurück und sag klar, dass sie nach dem Zurücksetzen nicht erneut gemessen wurde. Überschreite die Testzeit nicht, um sie erneut zu messen.

Zum Schluss:
18. Zeig die Vorher-/Nachher-Ergebnisse in einer kurzen Tabelle und erkläre für Anfänger verständlich, was du geändert hast und warum.
19. Sag klar, wenn die Temperaturen keine Zeit hatten, sich einzupendeln, und dass der Test dann zeigt, wie die Lüfter reagieren, aber nicht, wie warm das System über längere Zeit wird. Schlage bei Bedarf einen längeren Test vor.
20. Frag mich, bevor du die Konfiguration speicherst. Sag Bescheid, wenn etwas noch nicht gespeichert ist.

Wenn etwas schiefgeht oder unsicher wirkt, halte an und sag Bescheid.
""",
    "fr": """\
Je suis un nouvel utilisateur de Vifte et je veux configurer le contrôle des ventilateurs pour la première fois, puis le tester avec Stress.

Avant de modifier quoi que ce soit :
1. Vérifie que tu as accès à Vifte et à Stress. S'il manque quelque chose, dis-moi ce que je dois installer ou activer, et attends que ce soit fait.
2. Demande-moi à quoi je sers le PC et comment la machine doit se comporter en usage normal et sous forte charge. Demande aussi combien de temps le test doit durer au total. Attends ma réponse. Ne pose que les questions nécessaires, mais pose-les même si tu crois connaître la réponse grâce à des conversations précédentes.
3. Demande si Vifte doit aussi piloter les ventilateurs de la carte graphique, ou si la carte graphique doit les piloter elle-même. Explique que si Vifte les pilote, les ventilateurs doivent tourner en permanence, au moins à la vitesse minimale de Vifte. L'arrêt des ventilateurs de la carte graphique en usage léger ne fonctionne alors plus. Attends ma réponse.
4. Vérifie quelles configurations existent. Si « Claude » n'existe pas, demande si tu dois la créer pour moi et attends ma réponse. Dis-moi si ce sera une copie de la configuration active, et retire-lui les courbes et le contrôle des ventilateurs si je veux repartir de zéro. Si « Claude » existe, passe dessus. Confirme que « Claude » est active avant de continuer. Ne modifie pas les autres configurations.
5. Trouve quels capteurs et ventilateurs sont connectés, et dis-moi brièvement ce que tu as trouvé. Demande-moi ce qu'est chaque ventilateur et où il se trouve si tu ne peux pas le voir avec certitude. Ne devine pas à partir des noms, d'autres configurations ou de conversations précédentes. Demande aussi si la machine a un refroidissement liquide ou une pompe.

Ce à quoi tu ne dois pas toucher :
- Les pompes et tout ce qui est verrouillé.
- Les ventilateurs de la carte graphique, si j'ai choisi que la carte graphique les pilote. Utilise alors la température du GPU uniquement comme entrée pour les courbes des autres ventilateurs.

Configuration :
6. Si la machine a une pompe, elle doit être marquée comme pompe dans Vifte avant l'appairage. Tu ne peux pas la marquer toi-même. Si elle n'est pas marquée, explique-moi comment faire, et ne lance pas l'appairage avant d'avoir vérifié qu'elle est marquée. Si tu n'es pas sûr qu'une sortie soit une pompe, arrête-toi et demande-moi.
7. Préviens-moi avant de lancer l'appairage. Toutes les sorties sont testées, y compris les ventilateurs de la carte graphique : cela peut être bruyant et prendre plusieurs minutes. L'appairage ne change pas qui pilote les ventilateurs de la carte graphique.
8. Calibre uniquement les ventilateurs à piloter, et donne-leur des noms clairs.
9. Crée des courbes adaptées à mon usage, sur les températures les plus pertinentes, et associe-les aux ventilateurs.

Test et réglage :
10. Préviens-moi avant de lancer chaque test, car les ventilateurs peuvent être bruyants. Demande-moi aussi de fermer les programmes lourds et de laisser la machine tranquille pendant le test.
11. Crée un profil de test par phases qui reflète mon usage, avec une charge CPU et GPU propre à chaque type d'usage. Commence par un échauffement en usage léger, par exemple bureautique et photo/vidéo légère. Monte ensuite progressivement jusqu'à mes tâches les plus lourdes, et termine par une phase calme. Montre-moi le profil avant de commencer. Évalue cela toi-même à partir de mes réponses, sans utiliser une charge constante.
12. Répartis à parts égales le temps de test que j'ai indiqué sur deux séries avec le même profil. Si le temps est trop court pour que les phases donnent des mesures utiles, dis-le avant de commencer.
13. L'appairage et la calibration peuvent rendre la machine plus froide ou plus chaude que la normale. Attends donc que le CPU et le GPU soient stables au repos normal avant la première série.
14. Lance la première série. Relève les températures et la vitesse des ventilateurs à chaque phase, et estime le bruit à partir des RPM. Regarde surtout le silence dans les phases calmes, la rapidité de réaction des ventilateurs et s'ils oscillent.
15. Ajuste les courbes. Attends que le CPU et le GPU soient revenus à peu près à la température du début de la première série, puis lance la deuxième série pour comparer. Dis-moi si les températures ne sont pas entièrement redescendues.
16. L'appairage, la calibration, l'attente avant la première série et le refroidissement ne comptent pas dans le temps de test.
17. Si un réglage n'a pas aidé, reviens à la meilleure version mesurée, et dis clairement qu'elle n'a pas été remesurée après ce retour en arrière. Ne dépasse pas le temps de test pour la remesurer.

Pour finir :
18. Montre les résultats avant/après dans un court tableau et explique ce que tu as changé et pourquoi, de façon compréhensible pour un débutant.
19. Dis clairement si les températures n'ont pas eu le temps de se stabiliser, et que le test montre alors comment les ventilateurs réagissent, mais pas à quel point la machine chauffe sur la durée. Propose un test plus long si nécessaire.
20. Demande-moi avant d'enregistrer la configuration. Dis-moi si quelque chose n'est toujours pas enregistré.

Si quelque chose tourne mal ou semble incertain, arrête-toi et préviens-moi.
""",
    "es": """\
Soy un usuario nuevo de Vifte y quiero configurar el control de los ventiladores por primera vez y probarlo con Stress.

Antes de cambiar nada:
1. Comprueba que tienes acceso a Vifte y a Stress. Si falta algo, dime qué debo instalar o activar, y espera a que lo haya hecho.
2. Pregúntame para qué uso el PC y cómo debe comportarse el equipo en uso normal y con carga alta. Pregunta también cuánto debe durar la prueba en total. Espera mi respuesta. Haz solo las preguntas que necesites, pero pregunta aunque creas conocer la respuesta por conversaciones anteriores.
3. Pregunta si Vifte debe controlar también los ventiladores de la tarjeta gráfica, o si debe controlarlos la propia tarjeta. Explica que, si Vifte los controla, los ventiladores deben girar siempre, al menos a la velocidad mínima de Vifte. Entonces deja de funcionar la parada de ventiladores de la tarjeta en uso ligero. Espera mi respuesta.
4. Comprueba qué configuraciones existen. Si "Claude" no existe, pregunta si debes crearla para mí y espera mi respuesta. Dime si será una copia de la configuración activa, y elimina de ella las curvas y el control de ventiladores si quiero empezar de nuevo. Si "Claude" existe, cambia a ella. Confirma que "Claude" está activa antes de continuar. No cambies otras configuraciones.
5. Averigua qué sensores y ventiladores están conectados, y dime brevemente qué has encontrado. Pregúntame qué es cada ventilador y dónde está si no puedes saberlo con certeza. No adivines por los nombres, otras configuraciones o conversaciones anteriores. Pregunta también si el equipo tiene refrigeración líquida o una bomba.

Lo que no debes tocar:
- Las bombas y todo lo que esté bloqueado.
- Los ventiladores de la tarjeta gráfica, si he elegido que los controle la tarjeta. En ese caso, usa la temperatura de la GPU solo como entrada para las curvas de los demás ventiladores.

Configuración:
6. Si el equipo tiene una bomba, debe estar marcada como bomba en Vifte antes del emparejamiento. No puedes marcarla tú. Si no está marcada, explícame cómo hacerlo, y no inicies el emparejamiento hasta haber comprobado que está marcada. Si no estás seguro de si una salida es una bomba, detente y pregúntame.
7. Avísame antes de iniciar el emparejamiento. Se prueban todas las salidas, también los ventiladores de la tarjeta gráfica, así que puede haber mucho ruido y tardar varios minutos. El emparejamiento no cambia quién controla los ventiladores de la tarjeta gráfica.
8. Calibra solo los ventiladores que se vayan a controlar y dales nombres claros.
9. Crea curvas adecuadas a mi uso, con las temperaturas más relevantes, y conéctalas a los ventiladores.

Prueba y ajuste:
10. Avísame antes de iniciar cada prueba, porque los ventiladores pueden hacer ruido. Pídeme también que cierre los programas pesados y deje el equipo tranquilo durante la prueba.
11. Crea un perfil de prueba por fases que refleje mi uso, con carga propia de CPU y GPU para cada tipo de uso. Empieza con un calentamiento en uso ligero, por ejemplo ofimática y foto/vídeo ligeros. Aumenta después gradualmente hasta mis tareas más pesadas y termina con una fase tranquila. Muéstrame el perfil antes de empezar. Debes valorarlo tú a partir de mis respuestas, sin usar una carga constante.
12. Reparte por igual el tiempo de prueba que indiqué entre dos rondas con el mismo perfil. Si el tiempo es demasiado corto para que las fases den mediciones útiles, avísame antes de empezar.
13. El emparejamiento y la calibración pueden dejar el equipo más frío o más caliente de lo normal. Por eso, espera a que la CPU y la GPU estén estables en reposo normal antes de la primera ronda.
14. Ejecuta la primera ronda. Lee las temperaturas y la velocidad de los ventiladores en cada fase, y estima el ruido a partir de las RPM. Fíjate sobre todo en lo silencioso que es en las fases tranquilas, en lo rápido que reaccionan los ventiladores y en si suben y bajan constantemente.
15. Ajusta las curvas. Espera a que la CPU y la GPU vuelvan aproximadamente a la temperatura del inicio de la primera ronda, y ejecuta la segunda ronda para comparar. Avísame si las temperaturas no han bajado del todo.
16. El emparejamiento, la calibración, la espera antes de la primera ronda y el enfriamiento no cuentan en el tiempo de prueba.
17. Si un ajuste no ayudó, vuelve a la mejor versión medida, y di claramente que no se ha vuelto a medir tras la restauración. No superes el tiempo de prueba para medirla de nuevo.

Para terminar:
18. Muestra los resultados de antes y después en una tabla breve y explica qué cambiaste y por qué, de forma que lo entienda un principiante.
19. Di claramente si las temperaturas no tuvieron tiempo de estabilizarse, y que la prueba muestra entonces cómo reaccionan los ventiladores, pero no cuánto se calienta el equipo a largo plazo. Propón una prueba más larga si hace falta.
20. Pregúntame antes de guardar la configuración. Avísame si algo sigue sin guardarse.

Si algo sale mal o parece incierto, detente y avísame.
""",
}


def setup_prompt():
    return SETUP.get(current(), SETUP["en"])
