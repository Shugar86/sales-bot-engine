"""Tests for Decision Gate — v3 core module."""
import pytest
import random
from unittest.mock import patch
from src.core.decision_gate import DecisionGate, Decision
from src.core.context_reader import ChatContext, Message
from src.core.vibe_checker import VibeCheck


# Fixtures
@pytest.fixture
def default_gate():
    return DecisionGate(anti_spam_config={
        "min_delay_between_messages": 30,
        "max_delay_between_messages": 300,
        "leave_on_read": 0.35,
        "emoji_reaction": 0.15,
        "night_slowdown": 3.0,
        "night_start": 23,
        "night_end": 8,
    })


@pytest.fixture
def no_spam_gate():
    """Gate with no anti-spam — everything goes through."""
    return DecisionGate(anti_spam_config={
        "min_delay_between_messages": 0,
        "max_delay_between_messages": 0,
        "leave_on_read": 0.0,
        "emoji_reaction": 0.0,
    })


@pytest.fixture
def high_spam_gate():
    """Gate with high spam settings — most things leave-on-read."""
    return DecisionGate(anti_spam_config={
        "min_delay_between_messages": 30,
        "max_delay_between_messages": 300,
        "leave_on_read": 0.9,
        "emoji_reaction": 0.0,
    })


@pytest.fixture
def positive_vibe():
    return VibeCheck(
        should_respond=True,
        confidence=0.8,
        reason="Keyword match",
        suggested_angle="advice",
        match_type="keyword",
    )


@pytest.fixture
def negative_vibe():
    return VibeCheck(
        should_respond=False,
        confidence=0.7,
        reason="No match",
        suggested_angle="leave_on_read",
        match_type="none",
    )


@pytest.fixture
def casual_context():
    return ChatContext(
        messages=[
            Message(1, "u1", "a", "A", "Привет", 100),
        ],
        vibe="casual",
    )


@pytest.fixture
def directed_context():
    return ChatContext(
        messages=[
            Message(1, "u1", "a", "A", "Что думаешь?", 100, is_reply_to_me=True),
        ],
        is_directed_at_me=True,
        vibe="casual",
    )


class TestDecisionGateBasic:
    """Basic decision gate tests."""
    
    def test_dm_always_responds(self, no_spam_gate, negative_vibe, casual_context):
        decision = no_spam_gate.decide(
            vibe_check=negative_vibe,
            context=casual_context,
            is_dm=True,
        )
        assert decision.action == "respond"
    
    def test_dm_with_positive_vibe(self, no_spam_gate, positive_vibe, casual_context):
        decision = no_spam_gate.decide(
            vibe_check=positive_vibe,
            context=casual_context,
            is_dm=True,
        )
        assert decision.action == "respond"
    
    def test_group_negative_vibe_leaves_read(self, no_spam_gate, negative_vibe, casual_context):
        decision = no_spam_gate.decide(
            vibe_check=negative_vibe,
            context=casual_context,
            is_dm=False,
        )
        assert decision.action == "leave_read"
    
    def test_group_positive_vibe_responds(self, no_spam_gate, positive_vibe, casual_context):
        decision = no_spam_gate.decide(
            vibe_check=positive_vibe,
            context=casual_context,
            is_dm=False,
        )
        assert decision.action == "respond"


class TestDecisionGateDisengage:
    """Tests for disengage handling."""
    
    def test_disengage_on_go_away(self, no_spam_gate, positive_vibe):
        ctx = ChatContext(messages=[
            Message(1, "u1", "a", "A", "Отстань от меня!", 100),
        ])
        decision = no_spam_gate.decide(
            vibe_check=positive_vibe,
            context=ctx,
        )
        assert decision.action == "disengage"
        assert decision.confidence == 1.0
    
    def test_disengage_variants(self, no_spam_gate, positive_vibe):
        patterns = ["хватит", "не пиши", "уйди", "стоп", "перестань"]
        for pattern in patterns:
            ctx = ChatContext(messages=[
                Message(1, "u1", "a", "A", pattern, 100),
            ])
            decision = no_spam_gate.decide(positive_vibe, ctx)
            assert decision.action == "disengage", f"Failed for: {pattern}"


class TestDecisionGateAntiSpam:
    """Tests for anti-spam behavior."""
    
    def test_leave_on_read_probability(self, default_gate, positive_vibe, casual_context):
        """35% of messages should be leave-on-read."""
        random.seed(42)  # Fixed seed for reproducibility
        leave_count = 0
        total = 1000
        
        for _ in range(total):
            decision = default_gate.decide(
                vibe_check=positive_vibe,
                context=casual_context,
                is_dm=False,
            )
            if decision.action == "leave_read":
                leave_count += 1
        
        # Should be around 35% (with some tolerance)
        ratio = leave_count / total
        assert 0.25 <= ratio <= 0.45, f"Leave-on-read ratio: {ratio}"
    
    def test_emoji_reaction_probability(self, default_gate, positive_vibe, casual_context):
        """Some messages should get emoji reactions."""
        random.seed(123)
        react_count = 0
        total = 1000
        
        for _ in range(total):
            decision = default_gate.decide(
                vibe_check=positive_vibe,
                context=casual_context,
                is_dm=False,
            )
            if decision.action == "react":
                react_count += 1
        
        # Should be around 15% (after leave-on-read filtering)
        ratio = react_count / total
        assert ratio < 0.3, f"Emoji reaction ratio too high: {ratio}"
    
    def test_hourly_limit(self, no_spam_gate, positive_vibe, casual_context):
        decision = no_spam_gate.decide(
            vibe_check=positive_vibe,
            context=casual_context,
            is_dm=False,
            messages_in_last_hour=10,
            max_messages_per_hour=3,
        )
        assert decision.action == "leave_read"
        assert "лимит" in decision.reason
    
    def test_hourly_limit_not_exceeded(self, no_spam_gate, positive_vibe, casual_context):
        decision = no_spam_gate.decide(
            vibe_check=positive_vibe,
            context=casual_context,
            is_dm=False,
            messages_in_last_hour=2,
            max_messages_per_hour=3,
        )
        assert decision.action == "respond"


class TestDecisionGateDirectedAtMe:
    """Tests for directed-at-me behavior."""
    
    def test_directed_always_responds(self, default_gate, positive_vibe, directed_context):
        """Messages directed at me should almost always get a response."""
        random.seed(42)
        respond_count = 0
        
        for _ in range(100):
            decision = default_gate.decide(
                vibe_check=positive_vibe,
                context=directed_context,
                is_dm=False,
            )
            if decision.action == "respond":
                respond_count += 1
        
        # Directed messages should get response most of the time
        assert respond_count > 80  # > 80%
    
    def test_directed_sets_reply_to(self, no_spam_gate, positive_vibe, directed_context):
        decision = no_spam_gate.decide(
            vibe_check=positive_vibe,
            context=directed_context,
        )
        if decision.action == "respond":
            assert decision.reply_to is not None


class TestDecisionGateDelay:
    """Tests for delay calculation."""
    
    def test_delay_in_range(self, default_gate, positive_vibe, casual_context):
        random.seed(42)
        delays = []
        
        for _ in range(50):
            decision = default_gate.decide(
                vibe_check=positive_vibe,
                context=casual_context,
                is_dm=False,
            )
            if decision.action == "respond":
                delays.append(decision.delay_seconds)
        
        if delays:
            for delay in delays:
                assert 0 <= delay <= 900  # max with night slowdown
    
    def test_dm_delay_faster(self, default_gate, positive_vibe, casual_context):
        random.seed(42)
        dm_delays = []
        group_delays = []
        
        for _ in range(50):
            dm_decision = default_gate.decide(positive_vibe, casual_context, is_dm=True)
            group_decision = default_gate.decide(positive_vibe, casual_context, is_dm=False)
            
            if dm_decision.action == "respond":
                dm_delays.append(dm_decision.delay_seconds)
            if group_decision.action == "respond":
                group_delays.append(group_decision.delay_seconds)
        
        if dm_delays and group_delays:
            assert sum(dm_delays) / len(dm_delays) <= sum(group_delays) / len(group_delays)


class TestDecisionGateEmoji:
    """Tests for emoji selection."""
    
    def test_emoji_selected_on_react(self, default_gate, positive_vibe, casual_context):
        random.seed(999)
        
        for _ in range(200):
            decision = default_gate.decide(positive_vibe, casual_context)
            if decision.action == "react":
                assert decision.emoji is not None
                assert len(decision.emoji) > 0
                break
    
    def test_funny_emoji_on_laugh(self, default_gate, positive_vibe):
        ctx = ChatContext(messages=[
            Message(1, "u1", "a", "A", "хахаха 😂😂😂", 100),
        ])
        # Force emoji reaction
        with patch.object(default_gate, '_pick_emoji', return_value="😂"):
            decision = default_gate.decide(positive_vibe, ctx)
            if decision.action == "react":
                assert decision.emoji == "😂"


class TestDecisionGateShouldWait:
    """Tests for should_wait method."""
    
    def test_should_wait_recent(self, default_gate):
        assert default_gate.should_wait(10) is True  # 10 seconds < min_delay 30
    
    def test_should_not_wait_enough_time(self, default_gate):
        assert default_gate.should_wait(60) is False  # 60 seconds > min_delay 30
    
    def test_should_wait_boundary(self, default_gate):
        assert default_gate.should_wait(29) is True
        assert default_gate.should_wait(30) is False


class TestDecisionGateEdgeCases:
    """Edge case tests."""
    
    def test_empty_context(self, no_spam_gate, positive_vibe):
        ctx = ChatContext()
        decision = no_spam_gate.decide(positive_vibe, ctx)
        # Should handle gracefully
        assert decision.action in ["respond", "leave_read"]
    
    def test_none_last_message(self, no_spam_gate, negative_vibe):
        ctx = ChatContext(messages=[], is_directed_at_me=False)
        decision = no_spam_gate.decide(negative_vibe, ctx, is_dm=False)
        assert decision.action == "leave_read"  # Negative vibe or no message
    
    def test_zero_hourly_limit(self, no_spam_gate, positive_vibe, casual_context):
        decision = no_spam_gate.decide(
            positive_vibe, casual_context,
            messages_in_last_hour=0,
            max_messages_per_hour=0,
        )
        assert decision.action == "leave_read"
    
    def test_decision_has_reason(self, default_gate, positive_vibe, casual_context):
        decision = default_gate.decide(positive_vibe, casual_context)
        assert len(decision.reason) > 0
