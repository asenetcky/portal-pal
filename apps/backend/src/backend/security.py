"""
Security and PII Handling Patterns
Protecting LLM application in production
"""

import re

from langsmith import traceable


class InputSanitizer:
    """
    Sanitize user input before it reaches the LLM.
    Detects prompt injections patterns and cleans dangerous content.
    """

    INJECTION_PATTERNS = [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"forget\s+(all\s+)?previous",
        r"new\s+instructions:",
        r"system\s*prompt",
        r"---\s*end\s*(of)?\s*prompt",
        r"pretend\s+you\s+are",
        r"act\s+as\s+(if\s+)?you",
        r"bypass\s+(all\s+)?restrictions",
        r"reveal\s+(your|the)\s(system|instructions|prompt)",
        r"you\s+are\s+now\s+(DAN|jailbroken)",
    ]

    def __init__(self):
        self.patterns = [re.compile(p, re.IGNORECASE) for p in self.INJECTION_PATTERNS]

    def check(self, text: str) -> tuple[bool, str | None]:
        """
        Check if input is safe.
        Returns: (is_safe, rejection_reason)
        """

        for pattern in self.patterns:
            if pattern.search(text):
                return False, "Blocked: potential prompt injection detected."
        return True, None

    def clean(self, text: str) -> str:
        """Remove potentially dangerous delimiters from input."""

        text = re.sub(r"[-]{3,}", "", text)
        text = re.sub(r"[=]{3,}", "", text)
        text = text.replace("{{", "{ {").replace("}}", "} }")
        return text.strip()


class PIIDetector:
    """
    Detect and mask personally identifiable information.
    Works on BOTH input and output.
    """

    PATTERNS = {
        "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
        "phone": re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"),
        "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "credit_card": re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"),
        "ip_address": re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    }

    MASK_MAP = {
        "email": "[EMAIL REDACTED]",
        "phone": "[PHONE REDACTED]",
        "ssn": "[SSN REDACTED]",
        "credit_card": "[CREDIT CARD REDACTED]",
        "ip_address": "[IP ADDRESS REDACTED]",
    }

    def detect(self, text: str) -> dict[str, list[str]]:
        """Detect PII types present in text."""
        found = {}
        for pii_type, pattern in self.PATTERNS.items():
            matches = pattern.findall(text)
            if matches:
                found[pii_type] = matches
        return found

    def mask(self, text: str) -> str:
        """Replace all PII with redaction markers."""
        masked = text
        for pii_type, pattern in self.PATTERNS.items():
            masked = pattern.sub(self.MASK_MAP[pii_type], masked)
        return masked


class OutputValidator:
    """
    Validate LLM output before returning to the client.
    Catches PII leakage and harmful content in responses.
    """

    HARMFUL_PATTERNS = [
        re.compile(r"here('s| is) (how|the way) to (hack|steal|attack)", re.I),
        re.compile(r"password\s+is\s+", re.I),
        re.compile(r"api[_\s]?key\s*[:=]", re.I),
    ]

    def __init__(self):
        self.pii_detector = PIIDetector()

    def validate(self, output: str) -> tuple[str, list[str]]:
        """
        Validate and clean output.
        Returns: (cleaned_outputm list_of_warnings)
        """

        warnings = []

        # check for PII leakage in output
        pii_found = self.pii_detector.detect(output)
        if pii_found:
            output = self.pii_detector.mask(output)
            warnings.append(f"PII masked in output: {list(pii_found.keys())}")

        # check for harmful content
        for pattern in self.HARMFUL_PATTERNS:
            if pattern.search(output):
                output = "[Response blocked: potentially harmful content]"
                warnings.append("Harmful content blocked")
                break
        return output, warnings


class SecurityPipeline:
    """
    Full security pipeline that processes input and output.
    This is the single class you wire into your API.
    """

    def __init__(self):
        self.sanitizer = InputSanitizer()
        self.pii_detector = PIIDetector()
        self.output_validator = OutputValidator()

    @traceable(name="security_check_input")
    def check_input(self, text: str) -> tuple[bool, str, list[str | None]]:
        """
        Process input through security checks.
        Returns: (is_allowed, cleaned_text, security_notes)
        """

        notes = []

        # step 1. check for injection
        is_safe, reason = self.sanitizer.check(text)
        if not is_safe:
            return False, "", [reason]

        # step 2. clean input
        cleaned = self.sanitizer.clean(text)

        # step 3. massk pii before it hits llm
        pii_found = self.pii_detector.detect(cleaned)
        if pii_found:
            cleaned = self.pii_detector.mask(cleaned)
            notes.append(f"Input PII masked: {list(pii_found.keys())}")

        return True, cleaned, notes

    def check_output(self, text: str) -> tuple[str, list[str]]:
        """
        Validate output before returning to user.
        Returns: (cleaned_output, warnings)
        """

        return self.output_validator.validate(text)
