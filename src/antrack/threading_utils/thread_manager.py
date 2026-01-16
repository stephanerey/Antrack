import logging
import time
from dataclasses import dataclass
from typing import Dict, Any, Callable
from PyQt5.QtCore import QObject, QThread, pyqtSignal

@dataclass
class ThreadStats:
    name: str
    is_running: bool = False
    started_at: float = None
    finished_at: float = None
    last_duration_s: float = None
    total_runtime_s: float = 0.0
    start_count: int = 0
    last_error: str = None
    is_asyncio_loop: bool = False

class Worker(QObject):
    """Worker qui exécute une fonction dans un thread séparé"""
    status = pyqtSignal(str)
    error = pyqtSignal(str)
    result = pyqtSignal(object)
    finished = pyqtSignal()

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.abort = False

    def run(self):
        """Exécute la fonction et émet les signaux appropriés"""
        logger = logging.getLogger("ThreadManager.Worker")
        try:
            try:
                msg = f"START func={getattr(self.func, '__name__', str(self.func))}"
                self.status.emit(msg)
                logger.info(msg)
            except Exception:
                pass
            if not self.abort:
                result = self.func(*self.args, **self.kwargs)
                self.result.emit(result)
        except Exception as e:
            try:
                logger.error(f"ERROR func={getattr(self.func, '__name__', str(self.func))}: {e}")
            except Exception:
                pass
            self.error.emit(str(e))
        finally:
            try:
                msg = f"FINISH func={getattr(self.func, '__name__', str(self.func))}"
                self.status.emit(msg)
                logger.info(msg)
            except Exception:
                pass
            self.finished.emit()

class ThreadManager:
    """Gestionnaire de threads minimaliste"""

    def __init__(self):
        self.threads: Dict[str, QThread] = {}
        self.workers: Dict[str, Worker] = {}
        self.logger = logging.getLogger("ThreadManager")
        # Boucles asyncio persistantes (nom -> event loop)
        self.asyncio_loops: Dict[str, Any] = {}
        # Statistiques/diagnostics simples
        self.stats: Dict[str, "ThreadStats"] = {}

    def start_thread(self, thread_name: str, func: Callable, *args, **kwargs) -> Worker:
        """Démarre une fonction dans un thread séparé"""

        # Si un thread asyncio porte ce nom, on ne gère pas ici
        if thread_name in getattr(self, "asyncio_loops", {}) and self.asyncio_loops.get(thread_name) is not None:
            self.logger.info(f"'{thread_name}' est une boucle asyncio persistante → réutilisation")
            return self.workers.get(thread_name)

        if thread_name in self.threads:
            th = self.threads[thread_name]
            if th.isRunning():
                # → thread encore actif : on garde le comportement historique (réutiliser)
                self.logger.info(f"start_thread('{thread_name}'): thread encore actif → réutilisation")
                return self.workers[thread_name]
            else:
                # → thread fini : s'assurer du cleanup, puis recréer proprement
                self.logger.info(f"start_thread('{thread_name}'): ancien thread terminé → recréation")
                try:
                    # au cas où: forcer la sortie et attendre
                    th.quit()
                    th.wait(500)
                except Exception:
                    pass
                # retirer les entrées éventuelles
                self.threads.pop(thread_name, None)
                self.workers.pop(thread_name, None)
                # NB: _cleanup_thread() le fait normalement via les signaux,
                # mais on force ici le nettoyage synchrone pour éviter toute course.

        # Créer un nouveau thread et un worker
        thread = QThread()
        worker = Worker(func, *args, **kwargs)

        # Déplacer le worker dans le thread
        worker.moveToThread(thread)

        # Statistiques: initialiser/mettre à jour l'entrée
        stats = self._ensure_stats(thread_name)
        stats.is_running = True
        stats.started_at = time.time()
        stats.finished_at = None
        stats.start_count += 1
        stats.last_error = None

        # Connecter les signaux
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._cleanup_thread(thread_name))
        # Capturer la dernière erreur émise par le worker
        try:
            worker.error.connect(lambda msg, name=thread_name: self._record_error(name, msg))
        except Exception:
            pass
        # Journaliser les statuts du worker
        try:
            worker.status.connect(lambda msg, name=thread_name: self.logger.info(f"[{name}] {msg}"))
        except Exception:
            pass

        # Stocker le thread et le worker
        self.threads[thread_name] = thread
        self.workers[thread_name] = worker

        # Démarrer le thread
        thread.start()
        self.logger.info(f"Thread '{thread_name}' démarré")

        return worker

    def _cleanup_thread(self, thread_name: str):
        """Nettoie les références au thread terminé"""
        # Mettre à jour les stats
        stats = self.stats.get(thread_name)
        if stats and stats.is_running:
            stats.finished_at = time.time()
            if stats.started_at:
                stats.last_duration_s = max(0.0, stats.finished_at - stats.started_at)
                stats.total_runtime_s += stats.last_duration_s or 0.0
            stats.is_running = False

        if thread_name in self.threads:
            self.threads.pop(thread_name, None)
            self.workers.pop(thread_name, None)
            self.logger.debug(f"Thread '{thread_name}' nettoyé")

    def stop_thread(self, thread_name: str):
        """Arrête un thread spécifique"""
        # Si un event loop asyncio porte ce nom, demander son arrêt avant de quitter le thread
        if hasattr(self, "asyncio_loops") and thread_name in getattr(self, "asyncio_loops", {}):
            try:
                self.stop_asyncio_loop(thread_name)
            except Exception:
                pass

        if thread_name in self.workers:
            self.workers[thread_name].abort = True
            self.threads[thread_name].quit()
            self.threads[thread_name].wait(1000)  # Attendre 1s max
            self.logger.info(f"Thread '{thread_name}' arrêté")

    def stop_all_threads(self):
        """Arrête tous les threads"""
        # Arrêter d'abord toutes les boucles asyncio persistantes
        if hasattr(self, "asyncio_loops"):
            loop_names = list(self.asyncio_loops.keys())
            for loop_name in loop_names:
                try:
                    self.stop_asyncio_loop(loop_name)
                except Exception:
                    pass

        # Puis arrêter les QThreads/Workers
        thread_names = list(self.threads.keys())
        for thread_name in thread_names:
            self.stop_thread(thread_name)
        self.logger.info("Tous les threads ont été arrêtés")

    def get_worker(self, thread_name: str) -> Worker:
        """Récupère un worker par son nom"""
        return self.workers.get(thread_name)

    # ---- Diagnostics légers ----
    def _ensure_stats(self, thread_name: str) -> ThreadStats:
        st = self.stats.get(thread_name)
        if st is None:
            st = ThreadStats(name=thread_name)
            self.stats[thread_name] = st
        return st

    def _record_error(self, thread_name: str, msg: str):
        try:
            st = self._ensure_stats(thread_name)
            st.last_error = msg
        finally:
            try:
                self.logger.error(f"[{thread_name}] Worker error: {msg}")
            except Exception:
                pass

    def get_diagnostics(self) -> Dict[str, Any]:
        """
        Retourne un dict de stats par thread:
        { name: {is_running, start_count, last_duration_s, total_runtime_s, started_at, finished_at, last_error, is_asyncio_loop} }
        """
        out: Dict[str, Any] = {}
        for name, st in self.stats.items():
            out[name] = {
                'is_running': st.is_running,
                'is_asyncio_loop': st.is_asyncio_loop,
                'start_count': st.start_count,
                'last_duration_s': st.last_duration_s,
                'total_runtime_s': st.total_runtime_s,
                'started_at': st.started_at,
                'finished_at': st.finished_at,
                'last_error': st.last_error,
            }
        return out

    def diagnostics_summary(self) -> str:
        """
        Retourne un résumé textuel prêt à afficher dans une boîte de dialogue.
        """
        lines = []
        for name, st in self.stats.items():
            typ = "asyncio" if st.is_asyncio_loop else "thread"
            state = "RUNNING" if st.is_running else "STOPPED"
            last = f"{st.last_duration_s:.3f}s" if isinstance(st.last_duration_s, (int, float)) else "-"
            total = f"{st.total_runtime_s:.3f}s" if isinstance(st.total_runtime_s, (int, float)) else "0.000s"
            err = st.last_error or "-"
            lines.append(f"- {name} [{typ}] {state} | starts={st.start_count} | last={last} | total={total} | last_error={err}")
        if not lines:
            return "Aucune statistique de thread disponible."
        return "Diagnostics des threads:\n" + "\n".join(lines)

    # ---- Support asyncio ----
    def ensure_asyncio_loop(self, loop_name: str = "AxisCoreLoop", timeout: float = 5.0):
        """
        Démarre une boucle asyncio persistante dans un thread géré par ThreadManager si besoin,
        et attend qu'elle soit prête. Nettoie un éventuel thread/boucle précédents.
        """
        # Si une boucle est déjà enregistrée et active, rien à faire
        if loop_name in getattr(self, "asyncio_loops", {}) and self.asyncio_loops[loop_name] is not None:
            return

        # Si un thread du même nom existe encore, tenter de l'arrêter et le nettoyer
        if loop_name in self.threads:
            try:
                self.stop_thread(loop_name)
            except Exception:
                pass
            # Nettoyage défensif
            self.threads.pop(loop_name, None)
            self.workers.pop(loop_name, None)

        import threading
        import asyncio as _asyncio

        ready_event = threading.Event()

        def loop_entry():
            loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(loop)
            # Enregistrer la boucle et signaler qu'elle est prête
            self.asyncio_loops[loop_name] = loop
            try:
                ready_event.set()
            except Exception:
                pass
            try:
                loop.run_forever()
            finally:
                try:
                    loop.close()
                finally:
                    self.asyncio_loops.pop(loop_name, None)

        # Lancer la boucle via le ThreadManager
        self.start_thread(loop_name, loop_entry)
        # Marquer ce thread comme boucle asyncio dans les stats
        try:
            self._ensure_stats(loop_name).is_asyncio_loop = True
        except Exception:
            pass
        # Attendre que la boucle soit prête
        ready = ready_event.wait(timeout=timeout)
        if not ready:
            self.logger.error(f"La boucle asyncio '{loop_name}' n'a pas pu être initialisée dans le délai imparti")

    def run_coro(self, loop_name: str, coro_or_factory, timeout: float = None):
        """
        Exécute une coroutine sur la boucle asyncio persistante 'loop_name' et retourne son résultat.
        - coro_or_factory: soit une coroutine déjà créée, soit un callable qui retourne une coroutine.
        """
        import asyncio as _asyncio
        self.ensure_asyncio_loop(loop_name)
        loop = self.asyncio_loops.get(loop_name)
        if loop is None:
            raise RuntimeError(f"Boucle asyncio '{loop_name}' non initialisée")
        # Créer la coroutine après que la boucle soit prête si on a une fabrique
        try:
            coro = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
        except Exception as e:
            raise
        fut = _asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout) if timeout is not None else fut.result()

    def stop_asyncio_loop(self, loop_name: str):
        """
        Demande l'arrêt de la boucle asyncio persistante 'loop_name' si elle existe.
        """
        loop = self.asyncio_loops.get(loop_name)
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
