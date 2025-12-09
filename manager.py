"""
Soccer Scoreboard Plugin for LEDMatrix

Displays live, recent, and upcoming soccer games across multiple leagues including
Premier League, La Liga, Bundesliga, Serie A, Ligue 1, MLS, Champions League, and Europa League.
"""

import logging
import time
import threading
from typing import Dict, Any, Set, Optional

try:
    from src.plugin_system.base_plugin import BasePlugin
    from background_data_service import get_background_service
    from base_odds_manager import BaseOddsManager
except ImportError:
    BasePlugin = None
    get_background_service = None
    BaseOddsManager = None

# Import the manager classes
from soccer_managers import (
    SoccerLiveManager,
    SoccerRecentManager,
    SoccerUpcomingManager,
    create_premier_league_managers,
    create_la_liga_managers,
    create_bundesliga_managers,
    create_serie_a_managers,
    create_ligue_1_managers,
    create_mls_managers,
    create_champions_league_managers,
    create_europa_league_managers,
)

logger = logging.getLogger(__name__)

# League keys and display names
LEAGUE_KEYS = ['eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1', 'usa.1', 'uefa.champions', 'uefa.europa']
LEAGUE_NAMES = {
    'eng.1': 'Premier League',
    'esp.1': 'La Liga',
    'ger.1': 'Bundesliga',
    'ita.1': 'Serie A',
    'fra.1': 'Ligue 1',
    'usa.1': 'MLS',
    'uefa.champions': 'Champions League',
    'uefa.europa': 'Europa League'
}


class SoccerScoreboardPlugin(BasePlugin if BasePlugin else object):
    """
    Soccer scoreboard plugin using manager classes.

    This plugin provides soccer scoreboard functionality across multiple leagues
    by delegating to proven manager classes.
    """

    def __init__(
        self,
        plugin_id: str,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
        plugin_manager,
    ):
        """Initialize the soccer scoreboard plugin."""
        if BasePlugin:
            super().__init__(
                plugin_id, config, display_manager, cache_manager, plugin_manager
            )

        self.plugin_id = plugin_id
        self.config = config
        self.display_manager = display_manager
        self.cache_manager = cache_manager
        self.plugin_manager = plugin_manager

        self.logger = logger

        # Basic configuration
        self.is_enabled = config.get("enabled", True)
        # Get display dimensions from display_manager properties
        if hasattr(display_manager, 'matrix') and display_manager.matrix is not None:
            self.display_width = display_manager.matrix.width
            self.display_height = display_manager.matrix.height
        else:
            self.display_width = getattr(display_manager, "width", 128)
            self.display_height = getattr(display_manager, "height", 32)

        # League configurations
        self.logger.debug(f"Soccer plugin received config keys: {list(config.keys())}")
        
        # Check which leagues are enabled
        leagues_config = config.get('leagues', {})
        self.league_enabled = {}
        for league_key in LEAGUE_KEYS:
            league_config = leagues_config.get(league_key, {})
            self.league_enabled[league_key] = league_config.get('enabled', False)
            self.logger.debug(f"{LEAGUE_NAMES[league_key]} config: {league_config}")

        enabled_leagues = [k for k, v in self.league_enabled.items() if v]
        self.logger.info(
            f"League enabled states: {', '.join([LEAGUE_NAMES[k] for k in enabled_leagues]) if enabled_leagues else 'None'}"
        )

        # Global settings
        self.display_duration = float(config.get("display_duration", 30))
        self.game_display_duration = float(config.get("game_display_duration", 15))

        # Live priority per league
        self.league_live_priority = {}
        for league_key in LEAGUE_KEYS:
            league_config = leagues_config.get(league_key, {})
            self.league_live_priority[league_key] = league_config.get("live_priority", False)

        # Initialize background service if available
        self.background_service = None
        if get_background_service:
            try:
                self.background_service = get_background_service(
                    self.cache_manager, max_workers=1
                )
                self.logger.info("Background service initialized")
            except Exception as e:
                self.logger.warning(f"Could not initialize background service: {e}")

        # Initialize managers
        self._initialize_managers()

        # Mode cycling
        self.current_mode_index = 0
        self.last_mode_switch = 0
        self.modes = self._get_available_modes()

        self.logger.info(
            f"Soccer scoreboard plugin initialized - {self.display_width}x{self.display_height}"
        )
        self.logger.info(
            f"Enabled leagues: {', '.join([LEAGUE_NAMES[k] for k in enabled_leagues]) if enabled_leagues else 'None'}"
        )

        # Dynamic duration tracking
        self._dynamic_cycle_seen_modes: Set[str] = set()
        self._dynamic_mode_to_manager_key: Dict[str, str] = {}
        self._dynamic_manager_progress: Dict[str, Set[str]] = {}
        self._dynamic_managers_completed: Set[str] = set()
        self._dynamic_cycle_complete = False
        
        # Track current display context for granular dynamic duration
        self._current_display_league: Optional[str] = None  # 'eng.1', 'esp.1', etc.
        self._current_display_mode_type: Optional[str] = None  # 'live', 'recent', 'upcoming'

    def _initialize_managers(self):
        """Initialize all manager instances."""
        try:
            # Initialize managers for each enabled league
            for league_key in LEAGUE_KEYS:
                if not self.league_enabled.get(league_key, False):
                    continue
                
                league_config = self._adapt_config_for_manager(league_key)
                
                # Create managers based on league
                if league_key == 'eng.1':
                    self.eng1_live, self.eng1_recent, self.eng1_upcoming = create_premier_league_managers(
                        league_config, self.display_manager, self.cache_manager
                    )
                elif league_key == 'esp.1':
                    self.esp1_live, self.esp1_recent, self.esp1_upcoming = create_la_liga_managers(
                        league_config, self.display_manager, self.cache_manager
                    )
                elif league_key == 'ger.1':
                    self.ger1_live, self.ger1_recent, self.ger1_upcoming = create_bundesliga_managers(
                        league_config, self.display_manager, self.cache_manager
                    )
                elif league_key == 'ita.1':
                    self.ita1_live, self.ita1_recent, self.ita1_upcoming = create_serie_a_managers(
                        league_config, self.display_manager, self.cache_manager
                    )
                elif league_key == 'fra.1':
                    self.fra1_live, self.fra1_recent, self.fra1_upcoming = create_ligue_1_managers(
                        league_config, self.display_manager, self.cache_manager
                    )
                elif league_key == 'usa.1':
                    self.usa1_live, self.usa1_recent, self.usa1_upcoming = create_mls_managers(
                        league_config, self.display_manager, self.cache_manager
                    )
                elif league_key == 'uefa.champions':
                    self.champions_live, self.champions_recent, self.champions_upcoming = create_champions_league_managers(
                        league_config, self.display_manager, self.cache_manager
                    )
                elif league_key == 'uefa.europa':
                    self.europa_live, self.europa_recent, self.europa_upcoming = create_europa_league_managers(
                        league_config, self.display_manager, self.cache_manager
                    )
                
                self.logger.info(f"{LEAGUE_NAMES[league_key]} managers initialized")

        except Exception as e:
            self.logger.error(f"Error initializing managers: {e}", exc_info=True)

    def _adapt_config_for_manager(self, league_key: str) -> Dict[str, Any]:
        """
        Adapt plugin config format to manager expected format.

        Plugin uses: leagues: {eng.1: {...}, esp.1: {...}, ...}
        Managers expect: soccer_eng.1_scoreboard: {...}, soccer_esp.1_scoreboard: {...}, ...
        """
        leagues_config = self.config.get('leagues', {})
        league_config = leagues_config.get(league_key, {})
        
        self.logger.debug(f"DEBUG: league_config for {league_key} = {league_config}")

        # Extract nested configurations
        display_modes_config = league_config.get("display_modes", {})
        
        manager_display_modes = {
            f"soccer_{league_key}_live": display_modes_config.get("live", True),
            f"soccer_{league_key}_recent": display_modes_config.get("recent", True),
            f"soccer_{league_key}_upcoming": display_modes_config.get("upcoming", True),
        }

        # Create manager config with expected structure
        manager_config = {
            f"soccer_{league_key}_scoreboard": {
                "enabled": league_config.get("enabled", False),
                "favorite_teams": league_config.get("favorite_teams", []),
                "display_modes": manager_display_modes,
                "recent_games_to_show": league_config.get("recent_games_to_show", 5),
                "upcoming_games_to_show": league_config.get("upcoming_games_to_show", 10),
                "show_records": self.config.get("show_records", False),
                "show_ranking": self.config.get("show_ranking", False),
                "show_odds": self.config.get("show_odds", False),
                "update_interval_seconds": league_config.get(
                    "update_interval_seconds", 300
                ),
                "live_update_interval": league_config.get("live_update_interval", 30),
                "live_game_duration": league_config.get("live_game_duration", 20),
                "live_priority": league_config.get("live_priority", False),
                "show_favorite_teams_only": league_config.get("show_favorite_teams_only", False),
                "show_all_live": league_config.get("show_all_live", False),
                "filtering": {
                    "show_favorite_teams_only": league_config.get("show_favorite_teams_only", False),
                    "show_all_live": league_config.get("show_all_live", False),
                },
                "background_service": {
                    "request_timeout": 30,
                    "max_retries": 3,
                    "priority": 2,
                },
            }
        }

        # Add global config - get timezone from cache_manager's config_manager if available
        timezone_str = self.config.get("timezone")
        if not timezone_str and hasattr(self.cache_manager, 'config_manager'):
            timezone_str = self.cache_manager.config_manager.get_timezone()
        if not timezone_str:
            timezone_str = "UTC"
        
        # Get display config from main config if available
        display_config = self.config.get("display", {})
        if not display_config and hasattr(self.cache_manager, 'config_manager'):
            display_config = self.cache_manager.config_manager.get_display_config()
        
        manager_config.update(
            {
                "timezone": timezone_str,
                "display": display_config,
            }
        )
        
        self.logger.debug(f"Using timezone: {timezone_str} for {league_key} managers")

        return manager_config

    def _get_available_modes(self) -> list:
        """Get list of available display modes based on enabled leagues."""
        modes = []

        for league_key in LEAGUE_KEYS:
            if not self.league_enabled.get(league_key, False):
                continue
            
            leagues_config = self.config.get('leagues', {})
            league_config = leagues_config.get(league_key, {})
            display_modes = league_config.get("display_modes", {})
            
            prefix = f"soccer_{league_key}"
            if display_modes.get("live", True):
                modes.append(f"{prefix}_live")
            if display_modes.get("recent", True):
                modes.append(f"{prefix}_recent")
            if display_modes.get("upcoming", True):
                modes.append(f"{prefix}_upcoming")

        # Default to Premier League if no leagues enabled
        if not modes:
            modes = ["soccer_eng.1_live", "soccer_eng.1_recent", "soccer_eng.1_upcoming"]

        return modes

    def _get_current_manager(self):
        """Get the current manager based on the current mode."""
        if not self.modes:
            return None

        current_mode = self.modes[self.current_mode_index]
        
        # Parse mode: soccer_{league_key}_{mode_type}
        parts = current_mode.split('_', 2)
        if len(parts) < 3:
            return None
        
        league_key = parts[1]  # e.g., 'eng.1'
        mode_type = parts[2]  # 'live', 'recent', 'upcoming'
        
        if not self.league_enabled.get(league_key, False):
            return None
        
        # Get manager based on league and mode
        manager_attr = None
        if league_key == 'eng.1':
            manager_attr = f"eng1_{mode_type}"
        elif league_key == 'esp.1':
            manager_attr = f"esp1_{mode_type}"
        elif league_key == 'ger.1':
            manager_attr = f"ger1_{mode_type}"
        elif league_key == 'ita.1':
            manager_attr = f"ita1_{mode_type}"
        elif league_key == 'fra.1':
            manager_attr = f"fra1_{mode_type}"
        elif league_key == 'usa.1':
            manager_attr = f"usa1_{mode_type}"
        elif league_key == 'uefa.champions':
            manager_attr = f"champions_{mode_type}"
        elif league_key == 'uefa.europa':
            manager_attr = f"europa_{mode_type}"
        
        if manager_attr and hasattr(self, manager_attr):
            return getattr(self, manager_attr)
        
        return None

    def update(self) -> None:
        """Update soccer game data using parallel manager updates."""
        if not self.is_enabled:
            return

        # Collect all manager update tasks
        update_tasks = []
        
        for league_key in LEAGUE_KEYS:
            if not self.league_enabled.get(league_key, False):
                continue
            
            league_name = LEAGUE_NAMES[league_key]
            
            # Get manager attributes
            if league_key == 'eng.1':
                managers = [('eng1_live', self.eng1_live), ('eng1_recent', self.eng1_recent), ('eng1_upcoming', self.eng1_upcoming)]
            elif league_key == 'esp.1':
                managers = [('esp1_live', self.esp1_live), ('esp1_recent', self.esp1_recent), ('esp1_upcoming', self.esp1_upcoming)]
            elif league_key == 'ger.1':
                managers = [('ger1_live', self.ger1_live), ('ger1_recent', self.ger1_recent), ('ger1_upcoming', self.ger1_upcoming)]
            elif league_key == 'ita.1':
                managers = [('ita1_live', self.ita1_live), ('ita1_recent', self.ita1_recent), ('ita1_upcoming', self.ita1_upcoming)]
            elif league_key == 'fra.1':
                managers = [('fra1_live', self.fra1_live), ('fra1_recent', self.fra1_recent), ('fra1_upcoming', self.fra1_upcoming)]
            elif league_key == 'usa.1':
                managers = [('usa1_live', self.usa1_live), ('usa1_recent', self.usa1_recent), ('usa1_upcoming', self.usa1_upcoming)]
            elif league_key == 'uefa.champions':
                managers = [('champions_live', self.champions_live), ('champions_recent', self.champions_recent), ('champions_upcoming', self.champions_upcoming)]
            elif league_key == 'uefa.europa':
                managers = [('europa_live', self.europa_live), ('europa_recent', self.europa_recent), ('europa_upcoming', self.europa_upcoming)]
            else:
                continue
            
            for attr_name, manager in managers:
                if hasattr(self, attr_name) and manager:
                    update_tasks.append((f"{league_name} {attr_name.split('_')[1].title()}", manager.update))
        
        if not update_tasks:
            return
        
        # Run updates in parallel with individual error handling
        def run_update_with_error_handling(name: str, update_func):
            """Run a single manager update with error handling."""
            try:
                update_func()
            except Exception as e:
                self.logger.error(f"Error updating {name} manager: {e}", exc_info=True)
        
        # Start all update threads
        threads = []
        for name, update_func in update_tasks:
            thread = threading.Thread(
                target=run_update_with_error_handling,
                args=(name, update_func),
                daemon=True,
                name=f"Update-{name}"
            )
            thread.start()
            threads.append(thread)
        
        # Wait for all threads to complete with a reasonable timeout
        for thread in threads:
            thread.join(timeout=25.0)
            if thread.is_alive():
                self.logger.warning(
                    f"Manager update thread {thread.name} did not complete within timeout"
                )

    def display(self, display_mode: str = None, force_clear: bool = False) -> bool:
        """Display soccer games with mode cycling."""
        if not self.is_enabled:
            return False

        try:
            # If display_mode is provided, use it to determine which manager to call
            if display_mode:
                self.logger.debug(f"Display called with mode: {display_mode}")
                
                # Handle registered plugin mode names (soccer_live, soccer_recent, soccer_upcoming)
                if display_mode in ["soccer_live", "soccer_recent", "soccer_upcoming"]:
                    mode_type = display_mode.replace("soccer_", "")
                    # Route to all enabled leagues for this mode type
                    # For live mode, prioritize leagues with live content and live_priority enabled
                    managers_to_try = []
                    
                    for league_key in LEAGUE_KEYS:
                        if not self.league_enabled.get(league_key, False):
                            continue
                        
                        # For live mode, check if league has live priority and live content
                        if mode_type == 'live':
                            if not self.league_live_priority.get(league_key, False):
                                continue
                            
                            # Get live manager for this league
                            live_manager_attr = None
                            if league_key == 'eng.1':
                                live_manager_attr = 'eng1_live'
                            elif league_key == 'esp.1':
                                live_manager_attr = 'esp1_live'
                            elif league_key == 'ger.1':
                                live_manager_attr = 'ger1_live'
                            elif league_key == 'ita.1':
                                live_manager_attr = 'ita1_live'
                            elif league_key == 'fra.1':
                                live_manager_attr = 'fra1_live'
                            elif league_key == 'usa.1':
                                live_manager_attr = 'usa1_live'
                            elif league_key == 'uefa.champions':
                                live_manager_attr = 'champions_live'
                            elif league_key == 'uefa.europa':
                                live_manager_attr = 'europa_live'
                            
                            if live_manager_attr and hasattr(self, live_manager_attr):
                                live_manager = getattr(self, live_manager_attr)
                                if live_manager:
                                    live_games = getattr(live_manager, "live_games", [])
                                    if live_games:
                                        managers_to_try.append((league_key, live_manager))
                        else:
                            # For recent and upcoming modes, include all enabled leagues
                            if league_key == 'eng.1':
                                attr_name = f"eng1_{mode_type}"
                            elif league_key == 'esp.1':
                                attr_name = f"esp1_{mode_type}"
                            elif league_key == 'ger.1':
                                attr_name = f"ger1_{mode_type}"
                            elif league_key == 'ita.1':
                                attr_name = f"ita1_{mode_type}"
                            elif league_key == 'fra.1':
                                attr_name = f"fra1_{mode_type}"
                            elif league_key == 'usa.1':
                                attr_name = f"usa1_{mode_type}"
                            elif league_key == 'uefa.champions':
                                attr_name = f"champions_{mode_type}"
                            elif league_key == 'uefa.europa':
                                attr_name = f"europa_{mode_type}"
                            else:
                                continue
                            
                            if hasattr(self, attr_name):
                                manager = getattr(self, attr_name)
                                if manager:
                                    managers_to_try.append((league_key, manager))
                    
                    # Try each manager until one returns True (has content)
                    first_manager = True
                    for league_key, current_manager in managers_to_try:
                        if current_manager:
                            # Track which league we're displaying for granular dynamic duration
                            self._current_display_league = league_key
                            self._current_display_mode_type = mode_type
                            
                            # Only pass force_clear to the first manager
                            manager_force_clear = force_clear and first_manager
                            first_manager = False
                            
                            result = current_manager.display(manager_force_clear)
                            # If display returned True, we have content to show
                            if result is True:
                                try:
                                    self._record_dynamic_progress(current_manager)
                                except Exception as progress_err:
                                    self.logger.debug(
                                        "Dynamic progress tracking failed: %s", progress_err
                                    )
                                self._evaluate_dynamic_cycle_completion()
                                return result
                            # If result is False, try next manager
                            elif result is False:
                                continue
                            # If result is None or other, assume success
                            else:
                                return True
                    
                    # No manager returned True, return False
                    return False
                
                # Extract the mode type (live, recent, upcoming)
                mode_type = None
                if display_mode.endswith('_live'):
                    mode_type = 'live'
                elif display_mode.endswith('_recent'):
                    mode_type = 'recent'
                elif display_mode.endswith('_upcoming'):
                    mode_type = 'upcoming'
                
                if not mode_type:
                    self.logger.warning(f"Unknown display_mode: {display_mode}")
                    return False
                
                # Extract league from mode: soccer_{league_key}_{mode_type}
                parts = display_mode.split('_', 2)
                if len(parts) < 3:
                    self.logger.warning(f"Invalid display_mode format: {display_mode}")
                    return False
                
                league_key = parts[1]  # e.g., 'eng.1'
                
                # Get managers for this mode type across all enabled leagues
                managers_to_try = []
                for key in LEAGUE_KEYS:
                    if not self.league_enabled.get(key, False):
                        continue
                    
                    # Get manager attribute name
                    if key == 'eng.1':
                        attr_name = f"eng1_{mode_type}"
                    elif key == 'esp.1':
                        attr_name = f"esp1_{mode_type}"
                    elif key == 'ger.1':
                        attr_name = f"ger1_{mode_type}"
                    elif key == 'ita.1':
                        attr_name = f"ita1_{mode_type}"
                    elif key == 'fra.1':
                        attr_name = f"fra1_{mode_type}"
                    elif key == 'usa.1':
                        attr_name = f"usa1_{mode_type}"
                    elif key == 'uefa.champions':
                        attr_name = f"champions_{mode_type}"
                    elif key == 'uefa.europa':
                        attr_name = f"europa_{mode_type}"
                    else:
                        continue
                    
                    if hasattr(self, attr_name):
                        manager = getattr(self, attr_name)
                        if manager:
                            managers_to_try.append((key, manager))
                
                # Try each manager until one returns True (has content)
                first_manager = True
                for league_key, current_manager in managers_to_try:
                    if current_manager:
                        # Track which league we're displaying for granular dynamic duration
                        self._current_display_league = league_key
                        self._current_display_mode_type = mode_type
                        
                        # Only pass force_clear to the first manager
                        manager_force_clear = force_clear and first_manager
                        first_manager = False
                        
                        result = current_manager.display(manager_force_clear)
                        # If display returned True, we have content to show
                        if result is True:
                            try:
                                self._record_dynamic_progress(current_manager)
                            except Exception as progress_err:
                                self.logger.debug(
                                    "Dynamic progress tracking failed: %s", progress_err
                                )
                            self._evaluate_dynamic_cycle_completion()
                            return result
                        # If result is False, try next manager
                        elif result is False:
                            continue
                        # If result is None or other, assume success
                        else:
                            try:
                                self._record_dynamic_progress(current_manager)
                            except Exception as progress_err:
                                self.logger.debug(
                                    "Dynamic progress tracking failed: %s", progress_err
                                )
                            self._evaluate_dynamic_cycle_completion()
                            return True
                
                # No manager had content
                if not managers_to_try:
                    self.logger.warning(
                        f"No managers available for mode: {display_mode}"
                    )
                else:
                    self.logger.info(
                        f"No content available for mode: {display_mode} after trying {len(managers_to_try)} manager(s) - returning False"
                    )
                
                return False
            
            # Fall back to internal mode cycling if no display_mode provided
            current_time = time.time()

            # Check if we should stay on live mode
            should_stay_on_live = False
            if self.has_live_content():
                # Get current mode name
                current_mode = self.modes[self.current_mode_index] if self.modes else None
                # If we're on a live mode, stay there
                if current_mode and current_mode.endswith('_live'):
                    should_stay_on_live = True
                # If we're not on a live mode but have live content, switch to it
                elif not (current_mode and current_mode.endswith('_live')):
                    # Find the first live mode
                    for i, mode in enumerate(self.modes):
                        if mode.endswith('_live'):
                            self.current_mode_index = i
                            force_clear = True
                            self.last_mode_switch = current_time
                            self.logger.info(f"Live content detected - switching to display mode: {mode}")
                            break

            # Handle mode cycling only if not staying on live
            if not should_stay_on_live and current_time - self.last_mode_switch >= self.display_duration:
                self.current_mode_index = (self.current_mode_index + 1) % len(
                    self.modes
                )
                self.last_mode_switch = current_time
                force_clear = True

                current_mode = self.modes[self.current_mode_index]
                self.logger.info(f"Switching to display mode: {current_mode}")

            # Get current manager and display
            current_manager = self._get_current_manager()
            if current_manager:
                # Track which league/mode we're displaying for granular dynamic duration
                current_mode = self.modes[self.current_mode_index] if self.modes else None
                if current_mode:
                    parts = current_mode.split('_', 2)
                    if len(parts) >= 3:
                        self._current_display_league = parts[1]  # league_key
                        self._current_display_mode_type = parts[2]  # mode_type
                
                result = current_manager.display(force_clear)
                if result is not False:
                    try:
                        self._record_dynamic_progress(current_manager)
                    except Exception as progress_err:
                        self.logger.debug(
                            "Dynamic progress tracking failed: %s", progress_err
                        )
                self._evaluate_dynamic_cycle_completion()
                return result
            else:
                self.logger.warning("No manager available for current mode")
                return False

        except Exception as e:
            self.logger.error(f"Error in display method: {e}", exc_info=True)
            return False

    def has_live_priority(self) -> bool:
        """Check if any league has live priority enabled."""
        if not self.is_enabled:
            return False
        return any(
            self.league_enabled.get(league_key, False) and 
            self.league_live_priority.get(league_key, False)
            for league_key in LEAGUE_KEYS
        )

    def has_live_content(self) -> bool:
        """Check if any league has live content."""
        if not self.is_enabled:
            return False

        # Check each enabled league for live content
        for league_key in LEAGUE_KEYS:
            if not self.league_enabled.get(league_key, False):
                continue
            
            if not self.league_live_priority.get(league_key, False):
                continue
            
            # Get live manager for this league
            live_manager_attr = None
            if league_key == 'eng.1':
                live_manager_attr = 'eng1_live'
            elif league_key == 'esp.1':
                live_manager_attr = 'esp1_live'
            elif league_key == 'ger.1':
                live_manager_attr = 'ger1_live'
            elif league_key == 'ita.1':
                live_manager_attr = 'ita1_live'
            elif league_key == 'fra.1':
                live_manager_attr = 'fra1_live'
            elif league_key == 'usa.1':
                live_manager_attr = 'usa1_live'
            elif league_key == 'uefa.champions':
                live_manager_attr = 'champions_live'
            elif league_key == 'uefa.europa':
                live_manager_attr = 'europa_live'
            
            if live_manager_attr and hasattr(self, live_manager_attr):
                live_manager = getattr(self, live_manager_attr)
                if live_manager:
                    live_games = getattr(live_manager, "live_games", [])
                    if live_games:
                        favorite_teams = getattr(live_manager, "favorite_teams", [])
                        if favorite_teams:
                            has_favorite_live = any(
                                game.get("home_abbr") in favorite_teams
                                or game.get("away_abbr") in favorite_teams
                                for game in live_games
                            )
                            if has_favorite_live:
                                return True
                        else:
                            # No favorite teams configured, any live game counts
                            return True

        return False

    def get_live_modes(self) -> list:
        """
        Return the registered plugin mode name(s) that have live content.
        
        This should return the mode names as registered in manifest.json, not internal
        mode names. The plugin is registered with "soccer_live", "soccer_recent", "soccer_upcoming".
        """
        if not self.is_enabled:
            return []

        # Check if any league has live content
        has_any_live = self.has_live_content()
        
        if has_any_live:
            # Return the registered plugin mode name, not internal mode names
            # The plugin is registered with "soccer_live" in manifest.json
            return ["soccer_live"]
        
        return []

    def get_info(self) -> Dict[str, Any]:
        """Get plugin information."""
        try:
            current_manager = self._get_current_manager()
            current_mode = self.modes[self.current_mode_index] if self.modes else "none"

            # Build league info
            league_info = {}
            for league_key in LEAGUE_KEYS:
                league_info[league_key] = {
                    "enabled": self.league_enabled.get(league_key, False),
                    "live_priority": self.league_live_priority.get(league_key, False),
                }

            info = {
                "plugin_id": self.plugin_id,
                "name": "Soccer Scoreboard",
                "version": "2.0.0",
                "enabled": self.is_enabled,
                "display_size": f"{self.display_width}x{self.display_height}",
                "leagues": league_info,
                "current_mode": current_mode,
                "available_modes": self.modes,
                "display_duration": self.display_duration,
                "game_display_duration": self.game_display_duration,
                "show_records": self.config.get("show_records", False),
                "show_ranking": self.config.get("show_ranking", False),
                "show_odds": self.config.get("show_odds", False),
            }

            # Add manager-specific info if available
            if current_manager and hasattr(current_manager, "get_info"):
                try:
                    manager_info = current_manager.get_info()
                    info["current_manager_info"] = manager_info
                except Exception as e:
                    info["current_manager_info"] = f"Error getting manager info: {e}"

            return info

        except Exception as e:
            self.logger.error(f"Error getting plugin info: {e}")
            return {
                "plugin_id": self.plugin_id,
                "name": "Soccer Scoreboard",
                "error": str(e),
            }

    # ------------------------------------------------------------------
    # Dynamic duration hooks
    # ------------------------------------------------------------------
    def reset_cycle_state(self) -> None:
        """Reset dynamic cycle tracking."""
        if BasePlugin:
            super().reset_cycle_state()
        self._dynamic_cycle_seen_modes.clear()
        self._dynamic_mode_to_manager_key.clear()
        self._dynamic_manager_progress.clear()
        self._dynamic_managers_completed.clear()
        self._dynamic_cycle_complete = False

    def is_cycle_complete(self) -> bool:
        """Report whether the plugin has shown a full cycle of content."""
        if not self._dynamic_feature_enabled():
            return True
        self._evaluate_dynamic_cycle_completion()
        return self._dynamic_cycle_complete

    def _dynamic_feature_enabled(self) -> bool:
        """Return True when dynamic duration should be active."""
        if not self.is_enabled:
            return False
        return self.supports_dynamic_duration()
    
    def supports_dynamic_duration(self) -> bool:
        """
        Check if dynamic duration is enabled for the current display context.
        Checks granular settings: per-league/per-mode > per-league.
        """
        if not self.is_enabled:
            return False
        
        # If no current display context, return False (no global fallback)
        if not self._current_display_league or not self._current_display_mode_type:
            return False
        
        league_key = self._current_display_league
        mode_type = self._current_display_mode_type
        
        # Check per-league/per-mode setting first (most specific)
        leagues_config = self.config.get('leagues', {})
        league_config = leagues_config.get(league_key, {})
        league_dynamic = league_config.get("dynamic_duration", {})
        league_modes = league_dynamic.get("modes", {})
        mode_config = league_modes.get(mode_type, {})
        if "enabled" in mode_config:
            return bool(mode_config.get("enabled", False))
        
        # Check per-league setting
        if "enabled" in league_dynamic:
            return bool(league_dynamic.get("enabled", False))
        
        # No global fallback - return False
        return False
    
    def get_dynamic_duration_cap(self) -> Optional[float]:
        """
        Get dynamic duration cap for the current display context.
        Checks granular settings: per-league/per-mode > per-mode > per-league > global.
        """
        if not self.is_enabled:
            return None
        
        # If no current display context, check global setting
        if not self._current_display_league or not self._current_display_mode_type:
            if BasePlugin:
                return super().get_dynamic_duration_cap()
            return None
        
        league_key = self._current_display_league
        mode_type = self._current_display_mode_type
        
        # Check per-league/per-mode setting first (most specific)
        leagues_config = self.config.get('leagues', {})
        league_config = leagues_config.get(league_key, {})
        league_dynamic = league_config.get("dynamic_duration", {})
        league_modes = league_dynamic.get("modes", {})
        mode_config = league_modes.get(mode_type, {})
        if "max_duration_seconds" in mode_config:
            try:
                cap = float(mode_config.get("max_duration_seconds"))
                if cap > 0:
                    return cap
            except (TypeError, ValueError):
                pass
        
        # Check per-league setting
        if "max_duration_seconds" in league_dynamic:
            try:
                cap = float(league_dynamic.get("max_duration_seconds"))
                if cap > 0:
                    return cap
            except (TypeError, ValueError):
                pass
        
        # No global fallback - return None
        return None

    def _get_manager_for_mode(self, mode_name: str):
        """Resolve manager instance for a given display mode."""
        parts = mode_name.split('_', 2)
        if len(parts) < 3:
            return None
        
        league_key = parts[1]
        mode_type = parts[2]
        
        if not self.league_enabled.get(league_key, False):
            return None
        
        # Get manager attribute name
        if league_key == 'eng.1':
            attr_name = f"eng1_{mode_type}"
        elif league_key == 'esp.1':
            attr_name = f"esp1_{mode_type}"
        elif league_key == 'ger.1':
            attr_name = f"ger1_{mode_type}"
        elif league_key == 'ita.1':
            attr_name = f"ita1_{mode_type}"
        elif league_key == 'fra.1':
            attr_name = f"fra1_{mode_type}"
        elif league_key == 'usa.1':
            attr_name = f"usa1_{mode_type}"
        elif league_key == 'uefa.champions':
            attr_name = f"champions_{mode_type}"
        elif league_key == 'uefa.europa':
            attr_name = f"europa_{mode_type}"
        else:
            return None
        
        return getattr(self, attr_name, None)

    def _record_dynamic_progress(self, current_manager) -> None:
        """Track progress through managers/games for dynamic duration."""
        if not self._dynamic_feature_enabled() or not self.modes:
            self._dynamic_cycle_complete = True
            return

        current_mode = self.modes[self.current_mode_index]
        self._dynamic_cycle_seen_modes.add(current_mode)

        manager_key = self._build_manager_key(current_mode, current_manager)
        self._dynamic_mode_to_manager_key[current_mode] = manager_key

        total_games = self._get_total_games_for_manager(current_manager)
        if total_games <= 1:
            # Single (or no) game - treat as complete once visited
            self._dynamic_managers_completed.add(manager_key)
            return

        current_index = getattr(current_manager, "current_game_index", None)
        if current_index is None:
            # Fall back to zero if the manager does not expose an index
            current_index = 0
        identifier = f"index-{current_index}"

        progress_set = self._dynamic_manager_progress.setdefault(manager_key, set())
        progress_set.add(identifier)

        # Drop identifiers that no longer exist if game list shrinks
        valid_identifiers = {f"index-{idx}" for idx in range(total_games)}
        progress_set.intersection_update(valid_identifiers)

        if len(progress_set) >= total_games:
            self._dynamic_managers_completed.add(manager_key)

    def _evaluate_dynamic_cycle_completion(self) -> None:
        """Determine whether all enabled modes have completed their cycles."""
        if not self._dynamic_feature_enabled():
            self._dynamic_cycle_complete = True
            return

        if not self.modes:
            self._dynamic_cycle_complete = True
            return

        required_modes = [mode for mode in self.modes if mode]
        if not required_modes:
            self._dynamic_cycle_complete = True
            return

        for mode_name in required_modes:
            if mode_name not in self._dynamic_cycle_seen_modes:
                self._dynamic_cycle_complete = False
                return

            manager_key = self._dynamic_mode_to_manager_key.get(mode_name)
            if not manager_key:
                self._dynamic_cycle_complete = False
                return

            if manager_key not in self._dynamic_managers_completed:
                manager = self._get_manager_for_mode(mode_name)
                total_games = self._get_total_games_for_manager(manager)
                if total_games <= 1:
                    self._dynamic_managers_completed.add(manager_key)
                else:
                    self._dynamic_cycle_complete = False
                    return

        self._dynamic_cycle_complete = True

    @staticmethod
    def _build_manager_key(mode_name: str, manager) -> str:
        manager_name = manager.__class__.__name__ if manager else "None"
        return f"{mode_name}:{manager_name}"

    @staticmethod
    def _get_total_games_for_manager(manager) -> int:
        if manager is None:
            return 0
        for attr in ("live_games", "games_list", "recent_games", "upcoming_games"):
            value = getattr(manager, attr, None)
            if isinstance(value, list):
                return len(value)
        return 0

    def cleanup(self) -> None:
        """Clean up resources."""
        try:
            if hasattr(self, "background_service") and self.background_service:
                # Clean up background service if needed
                pass
            self.logger.info("Soccer scoreboard plugin cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
