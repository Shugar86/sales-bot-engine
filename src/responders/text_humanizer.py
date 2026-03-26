"""
Text Humanizer — makes generated text look like it was typed by a human.

Applies subtle modifications:
- Occasional lowercase starts
- Random typos (very subtle)
- Missing periods at end
- Casual abbreviations
"""

import random
from typing import Optional


# Common Russian typos (swap adjacent keys, miss common letters)
COMMON_TYPOS = {
    "привет": ["превет", "привет"],
    "спасибо": ["спасибо", "спасиьо"],
    "конечно": ["конечно", "конешно"],
    "проблема": ["проблема", "проблеима"],
    "собака": ["собака", "сабака"],
    "корм": ["корм", "коррм"],
    "может": ["может", "можеь"],
    "хорошо": ["хорошо", "харошо"],
    "точно": ["точно", "точн"],
}

# Casual abbreviations (real people use these)
ABBREVIATIONS = {
    "потому что": "потому что",  # keep as is (already casual)
    "конечно": "конечно",
}


class TextHumanizer:
    """Applies human-like modifications to generated text."""
    
    def __init__(
        self,
        typo_probability: float = 0.05,    # 5% chance of typo per word
        lowercase_start_probability: float = 0.15,  # 15% chance of lowercase start
        missing_period_probability: float = 0.20,   # 20% chance of missing final period
    ):
        self.typo_probability = typo_probability
        self.lowercase_start_probability = lowercase_start_probability
        self.missing_period_probability = missing_period_probability
    
    def humanize(self, text: str, is_casual: bool = False) -> str:
        """
        Apply human-like modifications to text.
        
        Args:
            text: Original text
            is_casual: If True, apply more casual modifications
        
        Returns:
            Humanized text
        """
        if not text or len(text) < 3:
            return text
        
        result = text
        
        # Lowercase start (more common in casual mode)
        if is_casual:
            chance = self.lowercase_start_probability * 2
        else:
            chance = self.lowercase_start_probability
        
        if random.random() < chance and result[0].isupper():
            result = result[0].lower() + result[1:]
        
        # Missing final period (casual mode)
        if is_casual and random.random() < self.missing_period_probability:
            result = result.rstrip()
            if result.endswith("."):
                result = result[:-1]
        
        # Random typos (very subtle, very rare)
        if random.random() < self.typo_probability:
            result = self._inject_typo(result)
        
        return result
    
    def _inject_typo(self, text: str) -> str:
        """Inject a subtle typo."""
        words = text.split()
        if len(words) < 2:
            return text
        
        # Pick a random word to typo (not first or last)
        idx = random.randint(1, len(words) - 2)
        word = words[idx]
        
        if len(word) < 3:
            return text
        
        # Check for known typo variants
        word_lower = word.lower()
        if word_lower in COMMON_TYPOS:
            variants = COMMON_TYPOS[word_lower]
            if len(variants) > 1:
                # Pick a typo variant
                typo = random.choice(variants)
                # Preserve original capitalization
                if word[0].isupper():
                    typo = typo[0].upper() + typo[1:]
                words[idx] = typo
                return " ".join(words)
        
        # Generic typo: swap two adjacent characters
        pos = random.randint(1, len(word) - 2)
        chars = list(word)
        chars[pos], chars[pos + 1] = chars[pos + 1], chars[pos]
        words[idx] = "".join(chars)
        
        return " ".join(words)


# Singleton
_humanizer: Optional[TextHumanizer] = None


def get_humanizer(**kwargs) -> TextHumanizer:
    """Get singleton humanizer."""
    global _humanizer
    if _humanizer is None:
        _humanizer = TextHumanizer(**kwargs)
    return _humanizer


def humanize_text(text: str, is_casual: bool = False) -> str:
    """Convenience function to humanize text."""
    return get_humanizer().humanize(text, is_casual)
