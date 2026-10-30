from django.db import migrations


def protect(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("""
        CREATE TRIGGER protect_historical_balance_adjustment
        BEFORE UPDATE OR DELETE ON banking_historicalbalanceadjustment
        FOR EACH ROW EXECUTE FUNCTION protect_previous_bank_archive();
        CREATE FUNCTION protect_bank_instruction() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' OR ROW(OLD.transaction_id, OLD.account_id, OLD.reference, OLD.product,
              OLD.operation, OLD.amount, OLD.request_digest) IS DISTINCT FROM ROW(NEW.transaction_id,
              NEW.account_id, NEW.reference, NEW.product, NEW.operation, NEW.amount, NEW.request_digest) THEN
            RAISE EXCEPTION 'Bank instruction binding is immutable';
          END IF;
          IF OLD.encrypted_request <> NEW.encrypted_request AND NEW.encrypted_request <> '' THEN
            RAISE EXCEPTION 'Bank instruction payload cannot be replaced';
          END IF;
          RETURN NEW;
        END; $$;
        CREATE TRIGGER protect_bank_instruction BEFORE UPDATE OR DELETE ON banking_instruction
        FOR EACH ROW EXECUTE FUNCTION protect_bank_instruction();
        CREATE FUNCTION protect_bank_notification() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' OR ROW(OLD.event, OLD.reference, OLD.account_id, OLD.amount, OLD.payload_digest)
            IS DISTINCT FROM ROW(NEW.event, NEW.reference, NEW.account_id, NEW.amount, NEW.payload_digest) THEN
            RAISE EXCEPTION 'Bank notification binding is immutable';
          END IF;
          RETURN NEW;
        END; $$;
        CREATE TRIGGER protect_bank_notification BEFORE UPDATE OR DELETE ON banking_notification
        FOR EACH ROW EXECUTE FUNCTION protect_bank_notification();
    """)


class Migration(migrations.Migration):
    dependencies = [("banking", "0004_bankaccount_opening_review_required_and_more")]
    operations = [migrations.RunPython(protect)]
