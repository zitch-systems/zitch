export type RegistrationName = {
  firstName: string;
  lastName: string;
};

/**
 * Normalize a full name once at registration so the OTP request and the
 * completed-account greeting use the same values. Everything after the first
 * word stays in lastName; this preserves compound family names without trying
 * to guess which part is a middle name.
 */
export function splitRegistrationName(value: string): RegistrationName {
  const parts = value.trim().split(/\s+/).filter(Boolean);
  return {
    firstName: parts[0] || '',
    lastName: parts.slice(1).join(' '),
  };
}

export function isCompleteRegistrationName(value: string): boolean {
  const { firstName, lastName } = splitRegistrationName(value);
  return firstName.length >= 2 && lastName.length >= 2;
}
