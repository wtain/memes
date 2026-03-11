import { useState, useEffect, Dispatch, SetStateAction } from "react";

// Installing use-debounce yields a conflict with typescript-eslint
// There is also use-debouncy library, but it's less trustworthy


export function useDebounce<Type>(value: Type, delay = 300): [Type, Dispatch<SetStateAction<Type>>] {
  const [debouncedValue, setDebouncedValue] = useState<Type>(value);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedValue(value);
    }, delay);

    return () => {
      clearTimeout(timer);
    };
  }, [value, delay]);

  return [debouncedValue, setDebouncedValue];
}