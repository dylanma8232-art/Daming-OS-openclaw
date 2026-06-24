import functools
import logging
import re
from typing import Callable, Any
from .memory.core import MemorySystem
from .events import bus, LogEvent

logger = logging.getLogger("daming_os.middleware")

# Global instances for the decorators to use
_global_memory = MemorySystem()

def attach_memory(auto_recall: bool = False):
    """
    Decorator to attach 大明记忆系统 3.0 to an Agent's processing function.
    Automatically injects context before the function runs and stores the result after.
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(agent_input: str, *args, **kwargs) -> Any:
            # 1. Retrieve Context if auto_recall is enabled
            if auto_recall:
                context = _global_memory.query(agent_input)
                kwargs["daming_os_context"] = context
            
            # 2. Execute Agent
            result = func(agent_input, *args, **kwargs)
            
            # 3. Store Memory (cleaning out <MemoryHint> tag and applying truncation safety)
            cleaned_input = re.sub(r"<MemoryHint>.*?</MemoryHint>", "", agent_input, flags=re.DOTALL)
            
            max_len = 1000
            cleaned_input_str = str(cleaned_input)
            if len(cleaned_input_str) > max_len:
                cleaned_input_str = cleaned_input_str[:max_len] + "... [Input Truncated]"
                
            result_str = str(result)
            if len(result_str) > max_len:
                result_str = result_str[:max_len] + "... [Result Truncated]"
                
            _global_memory.store(f"Input: {cleaned_input_str} | Result: {result_str}")
            return result
        return wrapper
    return decorator

def attach_growth():
    """
    Decorator to attach 大明成长系统 2.0 to an Agent's processing function.
    Automatically intercepts exceptions and publishes them to the Event Bus for GEP tracking.
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            try:
                result = func(*args, **kwargs)
                # Publish task success
                bus.publish(LogEvent(log_type="task_complete", content="Agent task completed successfully."))
                return result
            except Exception as e:
                # Intercept exception and trigger growth system
                logger.error(f"Agent execution failed. Publishing to Growth OS: {str(e)}")
                bus.publish(LogEvent(
                    log_type="task_failure", 
                    content=f"Exception caught in agent execution: {str(e)}"
                ))
                raise e
        return wrapper
    return decorator
