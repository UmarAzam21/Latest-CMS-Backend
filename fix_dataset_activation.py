#!/usr/bin/env python3
"""
Quick script to manually activate a stuck dataset.
"""
import sys
sys.path.insert(0, '.')

from app.db import engine

def activate_dataset(table_name: str):
    """Manually activate a dataset that's stuck in indexing."""
    conn = engine.raw_connection()
    try:
        cur = conn.cursor()
        
        # Check if table exists
        cur.execute(f"""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables 
                WHERE table_name = '{table_name}'
            )
        """)
        exists = cur.fetchone()[0]
        if not exists:
            print(f"ERROR: Table {table_name} does not exist!")
            return False
        
        print(f"✓ Table {table_name} exists")
        
        # Get current active dataset
        cur.execute("SELECT table_name FROM dataset_versions WHERE status = 'active'")
        row = cur.fetchone()
        previous_table = row[0] if row else None
        print(f"  Previous active table: {previous_table}")
        
        # Create or replace the view
        cur.execute(f'CREATE OR REPLACE VIEW current_dataset AS SELECT * FROM "{table_name}"')
        print(f"✓ Created view current_dataset")
        
        # Mark new dataset as active
        cur.execute(
            "UPDATE dataset_versions SET status = 'active', activated_at = now() WHERE table_name = %s",
            (table_name,),
        )
        print(f"✓ Marked {table_name} as active")
        
        # Retire previous table
        if previous_table:
            cur.execute(
                "UPDATE dataset_versions SET status = 'retired' WHERE table_name = %s",
                (previous_table,),
            )
            print(f"✓ Marked {previous_table} as retired")
        
        conn.commit()
        cur.close()
        print(f"\n✓ Dataset {table_name} successfully activated!")
        return True
        
    except Exception as e:
        print(f"ERROR: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

if __name__ == '__main__':
    table_name = 'dataset_1788271182948'
    activate_dataset(table_name)
