"""
Initialize built-in roles with module access on application startup.
This module creates default roles for viewing and updating each module.
"""

from sqlalchemy.orm import Session
from .models import Role


def init_builtin_roles(db: Session) -> None:
    """
    Create built-in roles for all modules if they don't exist.
    Each module gets a viewer role (read access) and an editor role (update access).
    """
    
    # Define all modules and create viewer + editor roles for each
    modules = [
        "support_system",
        "xlsx_import",
        "pages",
        "settings",
        "content",
        "messages",
        "leads",
        "roles",
        "users",
    ]
    
    builtin_roles = []

    # Default admin role grants all module access.
    builtin_roles.append({
        "name": "admin",
        "label": "Admin",
        "modules": [{module: "update" for module in modules}],
    })

    for module in modules:
        # Viewer role - read access only
        builtin_roles.append({
            "name": f"{module}_viewer",
            "label": f"{module.replace('_', ' ').title()} Viewer",
            "modules": [{module: "read"}],
        })

        # Editor role - update access (which includes read)
        builtin_roles.append({
            "name": f"{module}_editor",
            "label": f"{module.replace('_', ' ').title()} Editor",
            "modules": [{module: "update"}],
        })

    # Add global roles
    builtin_roles.extend([
        {
            "name": "global_viewer",
            "label": "Global Viewer",
            "modules": [{module: "read" for module in modules}],
        },
        {
            "name": "global_editor",
            "label": "Global Editor",
            "modules": [{module: "update" for module in modules}],
        },
    ])
    
    # Create roles if they don't exist
    for role_data in builtin_roles:
        existing = db.query(Role).filter(Role.name == role_data["name"]).first()
        if not existing:
            new_role = Role(
                name=role_data["name"],
                label=role_data["label"],
                modules=role_data["modules"],
            )
            db.add(new_role)
            print(f"✓ Created built-in role: {role_data['name']}")
    
    db.commit()
    print("✓ Built-in roles initialization complete")


def get_builtin_role_names() -> list[str]:
    """Get a list of all built-in role names."""
    modules = [
        "support_system",
        "xlsx_import",
        "pages",
        "settings",
        "content",
        "messages",
        "leads",
        "roles",
        "users",
    ]
    
    names = []
    for module in modules:
        names.append(f"{module}_viewer")
        names.append(f"{module}_editor")
    
    names.extend(["global_viewer", "global_editor"])
    return names
