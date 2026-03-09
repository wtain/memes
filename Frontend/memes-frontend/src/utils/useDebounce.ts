import { useState, useEffect } from "react";

// Installing use-debounce yields a conflict with typescript-eslint
// There is also use-debouncy library, but it's less trustworthy

export function useDebounce(value, delay = 300) {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedValue(value);
    }, delay);

    return () => {
      clearTimeout(timer);
    };
  }, [value, delay]);

  return debouncedValue;
}