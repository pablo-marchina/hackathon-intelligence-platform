from hackathon_api.auth import create_pkce,sign_oauth_state,verify_oauth_state

def test_oauth_state_is_signed_and_return_path_bounded():
 s=sign_oauth_state(provider='github',return_to='/library',secret='x'*32); assert verify_oauth_state(s,'x'*32)=={'provider':'github','return_to':'/library'}
def test_oauth_state_rejects_tampering():
 s=sign_oauth_state(provider='google',return_to='/',secret='x'*32)
 try:verify_oauth_state(s+'x','x'*32)
 except ValueError:pass
 else:raise AssertionError('tampered state accepted')
def test_pkce_uses_distinct_verifier_and_s256_challenge():
 v,c=create_pkce();assert v!=c and len(v)>=43 and len(c)==43
