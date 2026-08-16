import React from 'react';
import { Redirect, useLocalSearchParams } from 'expo-router';

/** Android App-Link landing route. The HTTPS URL can now open the installed app
 * directly; iOS and browsers keep using the backend's custom-scheme handoff. */
export default function WhatsAppApprovalAppLink() {
  const { token } = useLocalSearchParams<{ token?: string }>();
  return <Redirect href={{ pathname: '/waapprove', params: { token: token || '' } }} />;
}
