We want to move to CQRS. So cosmic needs a message bus.

The factories should build resources by composing functions (like I was going to do to begin with). Out of the box, a resource factory builds a resource by just taking the model, then applying the default mixins. Or perhaps we always supply the mixins.

The mixins are actually either command handlers or queries. They can be overriden by simply providing a function with the same signature and optionally calling the provided mixin.

Questions:
1. How do we provide the pydantic models used on the router? Are they involved in the definition of the commands? Are they dataclasses that are mapped to pydantic?

So maybe we just provide the dataclass-based commands as types? Then out of the box commands handlers, that can be overriden.


So:

# How could we construct a UoW with repositories for each query? Would that be used
# by the handlers?

Should you have to manually create the handlers so that they can be used elsewhere? I suppose only if you actually need them.

```
def override_get_object(id: str, Db: Link, UoW):
    with UoW() as uow:
        uow.scalars...
        or 
        uow.retrive(id)
    return 

resource = build_sqlalchemy_resource(
    Db=models.Link,
    UnitOfWork=Session (or custom UnitOfWork)

    # This isn't a command, it's a response?
    Read=Read

    # These can be used elsewhere in the system.
    Create=CreateCommand
    Update=UpdateCommand

    # Could this be a dataclass given to `retrieve_query_handler` or `list_query_handler` and
    # automatically bound to fastapi?
    Query=Query

    # overriding command handlers
    update_command=custom_update_command

    # Override where? Gets given query and something from the response?Argh tough
    get_where=override_get_where
    get_object=override_get_object,
)
```
