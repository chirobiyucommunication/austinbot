from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import re
import subprocess
import time

from bot.core.models import BotSettings, SlideDirection, TradeSignal
from bot.execution.adapters import BrokerAdapter, ExecutionResult

try:
    from selenium import webdriver
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver import ChromeOptions
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
except Exception:  # pragma: no cover
    webdriver = None
    TimeoutException = Exception
    ChromeOptions = object
    Service = object
    By = object
    Keys = object
    EC = object
    WebDriverWait = object


class PocketOptionSeleniumAdapter(BrokerAdapter):
    def __init__(self, settings: BotSettings) -> None:
        self.settings = settings
        self._driver = None

    @property
    def name(self) -> str:
        return "broker_plugin"

    def execute_signal(self, signal: TradeSignal, stake: float) -> ExecutionResult:
        if self.settings.broker_dry_run:
            return ExecutionResult(
                accepted=True,
                message=(
                    "Dry-run broker mode: Selenium click not sent. "
                    f"Would execute {signal.direction.value.upper()} {signal.pair} {signal.expiry} stake={stake}."
                ),
                pair=signal.pair,
                direction=signal.direction,
                expiry=signal.expiry,
                executed_at=datetime.utcnow(),
            )

        if webdriver is None:
            return ExecutionResult(
                accepted=False,
                message="Selenium is not installed. Install with: pip install selenium webdriver-manager",
                pair=signal.pair,
                direction=signal.direction,
                expiry=signal.expiry,
                executed_at=datetime.utcnow(),
            )

        try:
            driver = self._ensure_driver()
            self._ensure_page_open(driver)

            preparation_warnings: list[str] = []
            try:
                self._set_trade_amount(driver, stake)
            except Exception as exc:
                return ExecutionResult(
                    accepted=False,
                    message=f"Broker plugin execution failed: amount not applied ({exc})",
                    pair=signal.pair,
                    direction=signal.direction,
                    expiry=signal.expiry,
                    executed_at=datetime.utcnow(),
                )

            try:
                self._set_pair(driver, signal.pair)
            except Exception as exc:
                preparation_warnings.append(f"pair: {exc}")

            try:
                self._set_expiry(driver, signal.expiry)
            except Exception as exc:
                return ExecutionResult(
                    accepted=False,
                    message=f"Broker plugin execution failed: expiry not applied ({exc})",
                    pair=signal.pair,
                    direction=signal.direction,
                    expiry=signal.expiry,
                    executed_at=datetime.utcnow(),
                )

            self._click_direction(driver, signal.direction)

            warning_text = ""
            if preparation_warnings:
                warning_text = f" (prep warnings: {'; '.join(preparation_warnings)})"

            return ExecutionResult(
                accepted=True,
                message=(
                    f"Pocket Option Selenium executed {signal.direction.value.upper()} "
                    f"on {signal.pair} for {signal.expiry} with stake {stake}.{warning_text}"
                ),
                pair=signal.pair,
                direction=signal.direction,
                expiry=signal.expiry,
                executed_at=datetime.utcnow(),
            )
        except Exception as exc:
            return ExecutionResult(
                accepted=False,
                message=f"Broker plugin execution failed: {exc}",
                pair=signal.pair,
                direction=signal.direction,
                expiry=signal.expiry,
                executed_at=datetime.utcnow(),
            )

    def _ensure_driver(self):
        if self._driver is not None:
            return self._driver

        browser_profile_dir = self._browser_profile_dir()

        options = ChromeOptions()
        options.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-default-apps")
        options.add_argument("--disable-features=MediaRouter,OptimizationHints")
        options.add_argument("--no-first-run")
        options.add_argument("--log-level=3")
        options.add_argument("--start-maximized")
        if browser_profile_dir is not None:
            options.add_argument(f"--user-data-dir={browser_profile_dir}")
            options.add_argument("--profile-directory=Default")
        service = Service(log_output=subprocess.DEVNULL)
        service.creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        os.environ["WDM_LOG"] = "0"
        self._driver = webdriver.Chrome(service=service, options=options)
        return self._driver

    def _browser_profile_dir(self) -> Path | None:
        local_appdata = os.getenv("LOCALAPPDATA", "").strip()
        if local_appdata:
            preferred = Path(local_appdata) / "PocketOptionBot" / "data" / "browser_profile"
        else:
            preferred = Path.home() / "AppData" / "Local" / "PocketOptionBot" / "data" / "browser_profile"

        try:
            preferred.mkdir(parents=True, exist_ok=True)
            return preferred
        except Exception:
            pass

        try:
            fallback = Path.home() / "PocketOptionBot" / "browser_profile"
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback
        except Exception:
            return None

    def open_session(self) -> str:
        if webdriver is None:
            return "Selenium not installed. Run: pip install selenium webdriver-manager"
        try:
            driver = self._ensure_driver()
            self._ensure_page_open(driver)
            return "Browser session opened. Login manually, then run selector health check."
        except Exception as exc:
            return f"Failed to open browser session: {exc}"

    def selector_health_check(self) -> dict[str, bool]:
        checks: dict[str, bool] = {}
        if webdriver is None:
            return {"selenium": False}

        driver = self._ensure_driver()
        self._ensure_page_open(driver)

        selector_map = {
            "amount_input": self.settings.broker_selectors.get("amount_input", ""),
            "buy_button": self.settings.broker_selectors.get("buy_button", ""),
            "sell_button": self.settings.broker_selectors.get("sell_button", ""),
            "pair_dropdown": self.settings.broker_selectors.get("pair_dropdown", ""),
            "expiry_dropdown": self.settings.broker_selectors.get("expiry_dropdown", ""),
        }

        for key, selector in selector_map.items():
            if not selector:
                checks[key] = False
                continue
            try:
                matches = self._find_elements(driver, selector)
                checks[key] = len(matches) > 0
            except Exception:
                checks[key] = False

        return checks

    def is_logged_in(self) -> bool:
        if webdriver is None:
            return False

        try:
            driver = self._ensure_driver()
            current_url = (driver.current_url or "").lower()

            if "accounts.google.com" in current_url:
                return False

            if "pocketoption.com" not in current_url:
                return False

            if any(tag in current_url for tag in ["login", "sign", "auth"]):
                return False

            amount_selector = self.settings.broker_selectors.get("amount_input", "")
            buy_selector = self.settings.broker_selectors.get("buy_button", "")
            sell_selector = self.settings.broker_selectors.get("sell_button", "")

            def _has(selector: str) -> bool:
                if not selector:
                    return False
                try:
                    return len(self._find_elements(driver, selector)) > 0
                except Exception:
                    return False

            has_amount = _has(amount_selector)
            has_buy = _has(buy_selector)
            has_sell = _has(sell_selector)

            if has_amount and (has_buy or has_sell):
                return True

            if "cabinet" in current_url and (has_buy or has_sell):
                return True

            return False
        except Exception:
            return False

    def _ensure_page_open(self, driver) -> None:
        current_url = (driver.current_url or "").lower()
        if "pocketoption.com" in current_url and "/cabinet/" in current_url:
            return
        if self.settings.pocket_option_url not in driver.current_url:
            driver.get(self.settings.pocket_option_url)

    def _wait_clickable(self, driver, selector: str, timeout: int = 20):
        return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(self._locator_from_selector(selector)))

    def _wait_visible(self, driver, selector: str, timeout: int = 20):
        return WebDriverWait(driver, timeout).until(EC.visibility_of_element_located(self._locator_from_selector(selector)))

    def _locator_from_selector(self, selector: str):
        text = selector.strip()
        if text.lower().startswith("xpath="):
            return (By.XPATH, text.split("=", 1)[1].strip())
        return (By.CSS_SELECTOR, text)

    def _find_elements(self, driver, selector: str):
        by, value = self._locator_from_selector(selector)
        return driver.find_elements(by, value)

    def _set_trade_amount(self, driver, stake: float) -> None:
        configured = self.settings.broker_selectors.get("amount_input", "")
        candidates = [
            configured,
            "input[type='text'][autocomplete='off']",
            "input[type='text'][inputmode='decimal']",
            "input[name='amount']",
            "[data-test='trade-amount-input']",
            "xpath=//input[contains(@class,'amount') and not(@type='hidden')]",
            "xpath=//input[contains(@inputmode,'decimal') and not(@type='hidden')]",
        ]

        stake_text = (f"{stake:.8f}").rstrip("0").rstrip(".")
        if not stake_text:
            stake_text = str(stake)

        ranked_inputs: list[tuple[int, object]] = []

        for selector in candidates:
            if not selector:
                continue
            try:
                elements = self._find_elements(driver, selector)
            except Exception:
                continue

            for element in elements:
                try:
                    if not element.is_displayed() or not element.is_enabled():
                        continue
                    score = self._score_amount_input_candidate(element)
                    ranked_inputs.append((score, element))
                except Exception:
                    continue

        ranked_inputs.sort(key=lambda item: item[0], reverse=True)

        for _, element in ranked_inputs:
            if self._try_write_amount(driver, element, stake_text, float(stake)):
                return

        raise RuntimeError("Unable to set trade amount in broker UI")

    def _score_amount_input_candidate(self, element) -> int:
        score = 0
        attrs = [
            element.get_attribute("id") or "",
            element.get_attribute("name") or "",
            element.get_attribute("class") or "",
            element.get_attribute("placeholder") or "",
            element.get_attribute("aria-label") or "",
            element.get_attribute("data-test") or "",
        ]
        text = " ".join(attrs).lower()

        keywords = ("amount", "stake", "investment", "sum", "bet", "trade")
        for keyword in keywords:
            if keyword in text:
                score += 5

        value = (element.get_attribute("value") or "").strip()
        if self._parse_float(value) is not None:
            score += 2

        if (element.get_attribute("readonly") or "").lower() in {"true", "readonly"}:
            score -= 10

        return score

    def _try_write_amount(self, driver, element, stake_text: str, stake_value: float) -> bool:
        try:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", element)
            except Exception:
                pass

            try:
                element.click()
            except Exception:
                driver.execute_script("arguments[0].click();", element)

            element.send_keys(Keys.CONTROL, "a")
            element.send_keys(Keys.BACKSPACE)
            element.send_keys(stake_text)

            driver.execute_script(
                """
                const el = arguments[0];
                const v = arguments[1];
                if (el) {
                  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                  if (setter) {
                    setter.call(el, v);
                  } else {
                    el.value = v;
                  }
                  el.dispatchEvent(new Event('input', {bubbles: true}));
                  el.dispatchEvent(new Event('change', {bubbles: true}));
                  el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: 'Enter'}));
                  el.dispatchEvent(new Event('blur', {bubbles: true}));
                }
                """,
                element,
                stake_text,
            )

            current_value = element.get_attribute("value") or ""
            parsed = self._parse_float(current_value)
            if parsed is not None and abs(parsed - stake_value) < 0.001:
                return True

            if stake_text in current_value:
                return True

            return False
        except Exception:
            return False

    def _set_pair(self, driver, pair: str) -> None:
        pair_dropdown_selector = self.settings.broker_selectors.get("pair_dropdown")
        pair_search_selector = self.settings.broker_selectors.get("pair_search")
        pair_item_template = self.settings.broker_selectors.get("pair_item")

        if not pair_dropdown_selector or not pair_search_selector or not pair_item_template:
            return

        try:
            self._wait_clickable(driver, pair_dropdown_selector).click()
            search_input = self._wait_visible(driver, pair_search_selector)
            search_input.send_keys(Keys.CONTROL, "a")
            search_input.send_keys(pair)
            pair_item_selector = pair_item_template.format(pair=pair)
            self._wait_clickable(driver, pair_item_selector).click()
        except TimeoutException:
            pass

    def _set_expiry(self, driver, expiry: str) -> None:
        expiry_dropdown_selector = self.settings.broker_selectors.get("expiry_dropdown")
        expiry_item_template = self.settings.broker_selectors.get("expiry_item")
        target_tokens = self._expiry_tokens(expiry)
        target_seconds = self._expiry_to_seconds(expiry)

        if target_seconds is not None and self._is_expiry_target_applied(driver, target_seconds):
            return

        open_candidates = [
            expiry_dropdown_selector,
            ".expiration-select",
            "[data-test='expiration-select']",
            "xpath=//div[contains(@class,'expiration-select')]",
            "xpath=//div[contains(@class,'asset-panel')]//*[contains(@class,'expiration')]",
        ]

        for selector in open_candidates:
            if not selector:
                continue
            try:
                self._wait_clickable(driver, selector, timeout=4).click()
                break
            except Exception:
                continue

        if target_seconds is not None and self._set_expiry_via_time_inputs(driver, target_seconds):
            return

        if expiry_item_template:
            try:
                expiry_item_selector = expiry_item_template.format(expiry=expiry)
                self._wait_clickable(driver, expiry_item_selector, timeout=4).click()
                if target_seconds is None or self._is_expiry_target_applied(driver, target_seconds):
                    return
            except Exception:
                pass

        item_candidates = [
            "xpath=//*[self::li or self::div or self::span or self::button][contains(@class,'expiration') or contains(@class,'item') or contains(@class,'value')]",
            "xpath=//*[self::li or self::div or self::span or self::button][contains(normalize-space(),'sec') or contains(normalize-space(),'min') or contains(normalize-space(),':')]",
        ]

        for selector in item_candidates:
            try:
                for element in self._find_elements(driver, selector):
                    try:
                        if not element.is_displayed() or not element.is_enabled():
                            continue
                        text = ((element.text or "").strip()).lower()
                        if not text:
                            continue
                        if not any(token in text for token in target_tokens):
                            continue
                        try:
                            driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", element)
                        except Exception:
                            pass
                        try:
                            element.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", element)
                        if target_seconds is None or self._is_expiry_target_applied(driver, target_seconds):
                            return
                    except Exception:
                        continue
            except Exception:
                continue

        if target_seconds is not None and self._adjust_expiry_with_step_controls(driver, target_seconds):
            return

        raise RuntimeError(f"Unable to set expiry '{expiry}' in broker UI")

    def _expiry_tokens(self, expiry: str) -> tuple[str, ...]:
        value = (expiry or "").strip().upper()
        if value.startswith("S"):
            seconds = int(value[1:])
            return (
                f"{seconds} sec",
                f"{seconds}s",
                f"00:{seconds:02d}",
                f"00:00:{seconds:02d}",
            )
        if value.startswith("M"):
            minutes = value[1:]
            total_seconds = int(minutes) * 60
            return (
                f"{int(minutes)} min",
                f"{int(minutes)}m",
                f"{int(minutes)} minute",
                f"{total_seconds//60:02d}:{total_seconds%60:02d}",
                f"00:{total_seconds//60:02d}:{total_seconds%60:02d}",
            )
        return (value.lower(),)

    def _expiry_to_seconds(self, expiry: str) -> int | None:
        value = (expiry or "").strip().upper()
        if value.startswith("S"):
            return int(value[1:])
        if value.startswith("M"):
            return int(value[1:]) * 60
        return self._parse_expiry_text_to_seconds(value)

    def _set_expiry_via_time_inputs(self, driver, target_seconds: int) -> bool:
        minute_selector = self.settings.broker_selectors.get("expiry_minute_input", "").strip()
        second_selector = self.settings.broker_selectors.get("expiry_second_input", "").strip()

        minute_input = None
        second_input = None

        if minute_selector and second_selector:
            try:
                minute_candidates = self._find_elements(driver, minute_selector)
                second_candidates = self._find_elements(driver, second_selector)
                minute_input = self._first_visible_enabled(minute_candidates)
                second_input = self._first_visible_enabled(second_candidates)
            except Exception:
                minute_input = None
                second_input = None

        if minute_input is None or second_input is None:
            inputs = self._visible_time_inputs(driver)
            if len(inputs) >= 2:
                minute_input = inputs[0]
                second_input = inputs[1]

        if minute_input is None or second_input is None:
            return False

        minutes = target_seconds // 60
        seconds = target_seconds % 60

        ok_min = self._write_time_input(driver, minute_input, f"{minutes:02d}")
        ok_sec = self._write_time_input(driver, second_input, f"{seconds:02d}")
        if not (ok_min and ok_sec):
            return False

        time.sleep(0.08)
        return self._is_expiry_target_applied(driver, target_seconds)

    def _visible_time_inputs(self, driver) -> list:
        selectors = [
            "xpath=//input[@type='text' and @autocomplete='off']",
            "input[type='text'][autocomplete='off']",
        ]
        candidates: list = []
        for selector in selectors:
            try:
                elements = self._find_elements(driver, selector)
            except Exception:
                continue
            for element in elements:
                try:
                    if not element.is_displayed() or not element.is_enabled():
                        continue
                    value = (element.get_attribute("value") or "").strip()
                    maxlength = (element.get_attribute("maxlength") or "").strip()
                    if maxlength and self._parse_float(maxlength) is not None and int(float(maxlength)) <= 2:
                        candidates.append(element)
                        continue
                    if value.isdigit() and len(value) <= 2:
                        candidates.append(element)
                except Exception:
                    continue
            if len(candidates) >= 2:
                break
        return candidates

    def _write_time_input(self, driver, element, value: str) -> bool:
        try:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", element)
            except Exception:
                pass

            try:
                element.click()
            except Exception:
                driver.execute_script("arguments[0].click();", element)

            element.send_keys(Keys.CONTROL, "a")
            element.send_keys(Keys.BACKSPACE)
            element.send_keys(value)
            element.send_keys(Keys.TAB)

            driver.execute_script(
                """
                const el = arguments[0];
                const v = arguments[1];
                if (!el) return;
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                if (setter) {
                  setter.call(el, v);
                } else {
                  el.value = v;
                }
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: 'Enter'}));
                el.dispatchEvent(new Event('blur', {bubbles: true}));
                """,
                element,
                value,
            )

            current = (element.get_attribute("value") or "").strip()
            if current.endswith(value) or current == value:
                return True

            current_digits = re.sub(r"\D", "", current)
            target_digits = re.sub(r"\D", "", value)
            if current_digits and target_digits and int(current_digits) == int(target_digits):
                return True

            return False
        except Exception:
            return False

    @staticmethod
    def _first_visible_enabled(elements):
        for element in elements:
            try:
                if element.is_displayed() and element.is_enabled():
                    return element
            except Exception:
                continue
        return None

    def _is_expiry_target_applied(self, driver, target_seconds: int) -> bool:
        current_seconds = self._read_current_expiry_seconds(driver)
        if current_seconds is None:
            return False
        return current_seconds == target_seconds

    def _read_current_expiry_seconds(self, driver) -> int | None:
        selectors = [
            self.settings.broker_selectors.get("expiry_value", "").strip(),
            "xpath=//div[contains(@class,'expiration-select')]//*[self::span or self::div][string-length(normalize-space()) > 0]",
            "xpath=//*[contains(@class,'expiration') or contains(@class,'time') or contains(@class,'duration')][self::span or self::div]",
            "xpath=//span[contains(text(),':') or contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'sec') or contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'min')]",
        ]

        for selector in selectors:
            if not selector:
                continue
            try:
                elements = self._find_elements(driver, selector)
            except Exception:
                continue

            for element in elements:
                try:
                    if not element.is_displayed():
                        continue
                    text = (element.text or "").strip()
                    seconds = self._parse_expiry_text_to_seconds(text)
                    if seconds is not None:
                        return seconds
                except Exception:
                    continue

        return None

    def _parse_expiry_text_to_seconds(self, text: str) -> int | None:
        if not text:
            return None

        value = text.strip().lower()

        hhmmss = re.search(r"\b(\d{1,2}):(\d{2}):(\d{2})\b", value)
        if hhmmss:
            hours = int(hhmmss.group(1))
            minutes = int(hhmmss.group(2))
            seconds = int(hhmmss.group(3))
            return (hours * 3600) + (minutes * 60) + seconds

        mmss = re.search(r"\b(\d{1,2}):(\d{2})\b", value)
        if mmss:
            minutes = int(mmss.group(1))
            seconds = int(mmss.group(2))
            return (minutes * 60) + seconds

        sec_word = re.search(r"\b(\d+)\s*(sec|secs|second|seconds|s)\b", value)
        if sec_word:
            return int(sec_word.group(1))

        min_word = re.search(r"\b(\d+)\s*(min|mins|minute|minutes|m)\b", value)
        if min_word:
            return int(min_word.group(1)) * 60

        return None

    def _adjust_expiry_with_step_controls(self, driver, target_seconds: int) -> bool:
        plus = self._find_expiry_step_control(driver, increase=True)
        minus = self._find_expiry_step_control(driver, increase=False)
        if plus is None or minus is None:
            return False

        for _ in range(90):
            current_seconds = self._read_current_expiry_seconds(driver)
            if current_seconds is None:
                return False
            if current_seconds == target_seconds:
                return True

            control = plus if current_seconds < target_seconds else minus

            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", control)
            except Exception:
                pass

            try:
                control.click()
            except Exception:
                try:
                    driver.execute_script("arguments[0].click();", control)
                except Exception:
                    return False

            time.sleep(0.06)

        return self._is_expiry_target_applied(driver, target_seconds)

    def _find_expiry_step_control(self, driver, increase: bool):
        if increase:
            selectors = [
                self.settings.broker_selectors.get("expiry_plus", "").strip(),
                "xpath=//*[self::button or self::span or self::div][contains(@class,'btn-plus') or contains(@class,'plus') or @data-action='plus' or contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'plus')]",
            ]
        else:
            selectors = [
                self.settings.broker_selectors.get("expiry_minus", "").strip(),
                "xpath=//*[self::button or self::span or self::div][contains(@class,'btn-minus') or contains(@class,'minus') or @data-action='minus' or contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'minus')]",
            ]

        for selector in selectors:
            if not selector:
                continue
            try:
                elements = self._find_elements(driver, selector)
            except Exception:
                continue

            for element in elements:
                try:
                    if element.is_displayed() and element.is_enabled():
                        return element
                except Exception:
                    continue

        return None

    def _click_direction(self, driver, direction: SlideDirection) -> None:
        configured = self.settings.broker_selectors["buy_button"]
        fallbacks = [
            configured,
            "xpath=//span[contains(@class,'switch-state-block__item')][.//*[normalize-space()='Buy']]",
            "xpath=//span[contains(@class,'switch-state-block__item')][.//*[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'call')]]",
            "xpath=//span[contains(@class,'switch-state-block__item')][.//*[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'higher')]]",
            "xpath=//button[normalize-space()='Buy']",
        ]
        if direction == SlideDirection.SELL:
            configured = self.settings.broker_selectors["sell_button"]
            fallbacks = [
                configured,
                "xpath=//span[contains(@class,'switch-state-block__item')][.//*[normalize-space()='Sell']]",
                "xpath=//span[contains(@class,'switch-state-block__item')][.//*[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'put')]]",
                "xpath=//span[contains(@class,'switch-state-block__item')][.//*[contains(translate(normalize-space(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'lower')]]",
                "xpath=//button[normalize-space()='Sell']",
            ]

        last_exc: Exception | None = None
        for selector in fallbacks:
            if not selector:
                continue
            try:
                element = self._wait_clickable(driver, selector, timeout=6)
                if not self._direction_label_ok(driver, element, direction):
                    continue
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", element)
                except Exception:
                    pass

                try:
                    element.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", element)
                return
            except Exception as exc:
                last_exc = exc
                continue

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("No click selector available for direction")

    def _direction_label_ok(self, driver, element, direction: SlideDirection) -> bool:
        try:
            text = (element.text or "").strip().lower()
            if not text:
                text = (driver.execute_script("return (arguments[0].textContent || '').trim();", element) or "").lower()
        except Exception:
            text = ""

        if not text:
            return True

        buy_words = ("buy", "call", "higher")
        sell_words = ("sell", "put", "lower")

        if direction == SlideDirection.BUY:
            if any(word in text for word in sell_words):
                return False
            return any(word in text for word in buy_words)

        if any(word in text for word in buy_words):
            return False
        return any(word in text for word in sell_words)

    def get_market_price(self) -> float | None:
        if webdriver is None:
            return None

        try:
            driver = self._ensure_driver()
            if "pocketoption.com" not in (driver.current_url or ""):
                return None

            configured_selector = self.settings.broker_selectors.get("price_value", "").strip()
            candidates = [
                configured_selector,
                ".current-price",
                ".asset-price",
                ".value__val",
                "[data-test='current-asset-price']",
                "xpath=//*[contains(@class,'value__val')]",
            ]

            for selector in candidates:
                if not selector:
                    continue
                try:
                    elements = self._find_elements(driver, selector)
                except Exception:
                    continue
                for element in elements:
                    value = self._parse_float(element.text)
                    if value is not None and value > 0:
                        return value
            return None
        except Exception:
            return None

    def get_account_balance(self) -> float | None:
        if webdriver is None:
            return None

        try:
            driver = self._ensure_driver()
            if "pocketoption.com" not in (driver.current_url or ""):
                return None

            selectors = [
                self.settings.broker_selectors.get("balance_value", "").strip(),
                ".js-balance-demo",
                ".js-balance-real-NGN",
                ".js-balance-real-USD",
                "xpath=//span[contains(@class,'js-balance-demo')]",
            ]

            for selector in selectors:
                if not selector:
                    continue
                try:
                    elements = self._find_elements(driver, selector)
                except Exception:
                    continue

                for element in elements:
                    raw = (element.text or "").strip()
                    value = self._parse_float(raw)
                    if value is None:
                        attr_value = element.get_attribute("data-hd-show") or ""
                        value = self._parse_float(attr_value)
                    if value is not None:
                        return value

            return None
        except Exception:
            return None

    @staticmethod
    def _parse_float(text: str) -> float | None:
        if not text:
            return None
        cleaned = text.strip().replace(" ", "")
        cleaned = re.sub(r"[^0-9,.-]", "", cleaned)

        if cleaned.count(",") > 0 and cleaned.count(".") > 0:
            cleaned = cleaned.replace(",", "")
        elif cleaned.count(",") > 0 and cleaned.count(".") == 0:
            cleaned = cleaned.replace(",", ".")

        try:
            return float(cleaned)
        except Exception:
            return None
