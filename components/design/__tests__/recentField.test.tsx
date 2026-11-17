import React from 'react';
import renderer, { act } from 'react-test-renderer';
import { TextInput } from 'react-native';
import { RecentField } from '../billkit';

jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Naira', () => ({ Naira: () => null }));
jest.mock('@/components/design/ui', () => ({ money: (v: number) => `₦${v}` }));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { surface: '#fff', surface2: '#f4f9f8', brand: '#066E66', ink1: '#06231F', ink3: '#526863', line: '#E2EEEB' }, theme: 'light' }),
  font: { regular: 'r', bold: 'b' },
}));

const mount = (props: Partial<React.ComponentProps<typeof RecentField>> = {}) => {
  const onChangeText = jest.fn();
  let tree!: renderer.ReactTestRenderer;
  act(() => {
    tree = renderer.create(
      <RecentField
        label="Phone number"
        value="08012345678"
        onChangeText={onChangeText}
        onPick={jest.fn()}
        items={[{ value: '08012345678', title: '0801 234 5678', sub: 'MTN · My number' }]}
        menuTitle="Numbers you've topped up"
        anotherLabel="Use another number"
        tag="My number"
        format={(v) => v.replace(/^(\d{4})(\d{3})(\d+)$/, '$1 $2 $3')}
        {...props}
      />,
    );
  });
  return { tree, onChangeText, input: () => tree.root.findByType(TextInput) };
};

describe('RecentField (phone, meter and smartcard numbers)', () => {
  it('opens the number keyboard by default, without autocorrect', () => {
    const { input } = mount();
    expect(input().props.keyboardType).toBe('number-pad');
    expect(input().props.autoCorrect).toBe(false);
    expect(input().props.spellCheck).toBe(false);
  });

  it('lets a number be typed straight in', () => {
    const { input, onChangeText } = mount();
    act(() => input().props.onChangeText('08035550192'));
    expect(onChangeText).toHaveBeenCalledWith('08035550192');
  });

  it('puts the cursor in the field when it is tapped anywhere, not only on the digits', () => {
    const { tree, input } = mount();
    const focus = jest.spyOn(input().instance, 'focus');
    act(() => tree.root.findByProps({ testID: 'recent-field-box' }).props.onPress());
    expect(focus).toHaveBeenCalledTimes(1);
  });

  it('offers the phone number to autofill on phone fields only', () => {
    expect(mount({ autoComplete: 'tel' }).input().props.textContentType).toBe('telephoneNumber');
    expect(mount().input().props.textContentType).toBeUndefined();
  });

  it('keeps a letters keyboard where a caller asks for one (betting IDs)', () => {
    expect(mount({ keyboardType: 'default' }).input().props.keyboardType).toBe('default');
  });
});
